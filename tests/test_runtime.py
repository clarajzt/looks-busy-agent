"""Synthetic regressions: no credentials, real mailbox, scheduler or model calls."""
from __future__ import annotations

import argparse
import contextlib
import copy
import importlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/looks-busy-agent/scripts"
sys.path.insert(0, str(SCRIPTS))
collector = importlib.import_module("collect_email")
imap_text = importlib.import_module("imap_text")
runner = importlib.import_module("run_daily")
checker = importlib.import_module("check_config")
saver = importlib.import_module("save_report")
doctor = importlib.import_module("doctor")
EXAMPLE = json.loads((SCRIPTS.parent / "references/config.example.json").read_text())
DAY = date(2026, 9, 3)
BODY = "【AI日记】2026-09-03\n\n今日推进\n完成示例需求清单整理。\n\n明日计划\n计划复核示例清单。"
TEXT = '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 32 1 NIL NIL)'
ATTACHMENT = '("APPLICATION" "PDF" ("NAME" "private.pdf") NIL NIL "BASE64" 99999 NIL ("ATTACHMENT" ("FILENAME" "private.pdf")))'
EML = '("MESSAGE" "RFC822" ("NAME" "attached.eml") NIL NIL "7BIT" 999 NIL ' + TEXT + ' 1 NIL ("ATTACHMENT" NIL))'


class FakeIMAP:
    def __init__(self, structure=None, mailbox_ok=True, internal='03-Sep-2026 08:00:00 +0000'):
        self.structure = structure or f'({TEXT}{ATTACHMENT}{EML} "MIXED" NIL NIL)'
        self.mailbox_ok = mailbox_ok
        self.internal = internal
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def login(self, *_):
        return "OK", []

    def select(self, mailbox, readonly=False):
        assert readonly, "mailbox must be read-only"
        return ("OK" if self.mailbox_ok else "NO"), []

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "search":
            return "OK", [b"7"]
        query = args[-1]
        if query == "(INTERNALDATE BODYSTRUCTURE)":
            return "OK", [f'1 (UID 7 INTERNALDATE "{self.internal}" BODYSTRUCTURE {self.structure})'.encode()]
        if "HEADER.FIELDS" in query:
            return "OK", [(b"1 (BODY[HEADER] {90}", b"Date: Thu, 3 Sep 2026 08:00:00 +0000\r\nSubject: Synthetic progress\r\n\r\n"), b")"]
        assert "BODY.PEEK[1]" in query, f"unexpected body fetch: {query}"
        return "OK", [(b"1 (BODY[1] {17}", b"Synthetic progress"), b")"]


class CollectorTests(unittest.TestCase):
    def test_only_selected_text_is_downloaded(self):
        conn = FakeIMAP()
        records = collector.fetch_mailbox(conn, "INBOX", DAY, 10, 1000, "Asia/Shanghai")
        self.assertEqual(records[0]["body"], "Synthetic progress")
        self.assertTrue(records[0]["has_attachments"])
        self.assertNotIn("BODY.PEEK[]", repr(conn.calls))
        self.assertNotIn("BODY.PEEK[2]", repr(conn.calls))
        self.assertNotIn("BODY.PEEK[3]", repr(conn.calls))

    def test_attached_multipart_subtree_is_pruned(self):
        parts, attached = imap_text.select_text_parts([imap_text.parse_structure([f'BODYSTRUCTURE {TEXT}'.encode()]), "MIXED", None, ["ATTACHMENT", None]])
        self.assertEqual(parts, [])
        self.assertTrue(attached)

    def test_attached_email_body_never_enters_summary(self):
        message = collector.message_from_bytes(b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nouter\r\n--b\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment\r\n\r\nSubject: private\r\nContent-Type: text/plain\r\n\r\nattached secret\r\n--b--\r\n', policy=collector.policy.default)
        self.assertNotIn("attached secret", collector.body_text(message, 1000))

    def test_unknown_charset_and_malformed_structure_degrade(self):
        self.assertEqual(imap_text.decode_bytes(b"hello", "unknown-codec"), "hello")
        conn = FakeIMAP(structure="NIL")
        record = collector.fetch_mailbox(conn, "INBOX", DAY, 10, 1000)[0]
        self.assertEqual(record["body"], "")
        self.assertTrue(record["warnings"])

    def test_timezone_filters_before_body_fetch(self):
        conn = FakeIMAP(internal="02-Sep-2026 20:00:00 +0000")
        self.assertEqual(len(collector.fetch_mailbox(conn, "INBOX", DAY, 10, 1000, "Asia/Shanghai")), 1)
        conn = FakeIMAP(internal="03-Sep-2026 20:00:00 +0000")
        self.assertEqual(collector.fetch_mailbox(conn, "INBOX", DAY, 10, 1000, "Asia/Shanghai"), [])
        self.assertNotIn("BODY.PEEK[", repr(conn.calls))

    def test_literal_and_alternative_structure(self):
        raw = b'BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" {5}\r\nUTF-8) NIL NIL "7BIT" 10 1 NIL NIL)'
        self.assertEqual(imap_text.select_text_parts(imap_text.parse_structure([raw]))[0][0][2], "UTF-8")
        literal_parts = [(b'BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" {5}', b'UTF-8'), b') NIL NIL "7BIT" 10 1 NIL NIL)']
        self.assertEqual(imap_text.select_text_parts(imap_text.parse_structure(literal_parts))[0][0][2], "UTF-8")
        html = TEXT.replace('"PLAIN"', '"HTML"')
        structure = imap_text.parse_structure([f'BODYSTRUCTURE ({html}{TEXT} "ALTERNATIVE" NIL NIL)'.encode()])
        self.assertEqual(imap_text.select_text_parts(structure)[0][0][0], "2")

    def test_disabled_source_never_reads_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(EXAMPLE))
            with patch.object(sys, "argv", ["collect_email.py", "--config", str(path)]), patch.object(collector, "resolve_credentials") as credentials:
                with self.assertRaises(SystemExit):
                    collector.main()
                credentials.assert_not_called()

    def test_failed_mailbox_check_returns_failure(self):
        config = copy.deepcopy(EXAMPLE)
        config["sources"]["email"]["enabled"] = True
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(config))
            with patch.object(sys, "argv", ["collect_email.py", "--config", str(path), "--check"]), patch.object(collector, "resolve_credentials", return_value=("synthetic", "synthetic")), patch.object(collector.imaplib, "IMAP4_SSL", return_value=FakeIMAP(mailbox_ok=False)):
                with self.assertRaises(SystemExit):
                    collector.main()


class ConfigTests(unittest.TestCase):
    def test_permission_flags_are_booleans(self):
        for section, key in (("email", "enabled"), ("feishu", "enabled")):
            config = copy.deepcopy(EXAMPLE)
            config["sources"][section][key] = "false"
            self.assertTrue(checker.check(config)[0])
        config = copy.deepcopy(EXAMPLE)
        config["calendar"]["write_enabled"] = "false"
        self.assertTrue(checker.check(config)[0])

    def test_types_timezone_and_ancestor_root_rejected(self):
        for value in (None, [], "invalid"):
            self.assertTrue(checker.check(value)[0])
            config = copy.deepcopy(EXAMPLE)
            config["sources"] = value
            self.assertTrue(checker.check(config)[0])
        config = copy.deepcopy(EXAMPLE)
        config["timezone"] = "Not/A_Timezone"
        self.assertTrue(checker.check(config)[0])
        config = copy.deepcopy(EXAMPLE)
        config["sources"]["local_files"].update(enabled=True, roots=[str(Path.home().anchor)])
        self.assertTrue(checker.check(config)[0])

    def test_doctor_stops_on_invalid_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"sources": null}')
            report = doctor.Report()
            self.assertIsNone(doctor.load_config(report, path))
            self.assertEqual(report.items[0]["status"], "fail")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = copy.deepcopy(EXAMPLE)
        self.config["report"]["output_dir"] = str(self.directory / "custom reports")
        self.config["sources"]["feishu"].update(enabled=True, calendar_id="work-calendar")
        self.path = self.directory / "config.json"
        self.path.write_text(json.dumps(self.config))
        self.args = argparse.Namespace(config=self.path, data_dir=self.directory / "data", date=DAY.isoformat(), agent="claude", check_local_timezone=False, scheduled=False)

    def fake_process(self, command, **kwargs):
        if command[0] == "lark-cli":
            self.assertIn("work-calendar", command)
            self.assertIn("2026-09-03T00:00:00+08:00", command)
            self.assertIn("2026-09-04T00:00:00+08:00", command)
            return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": {"events": [{"summary": "Synthetic review"}]}}), "")
        self.assertEqual(command[0], "claude")
        self.assertEqual(command[command.index("--allowedTools") + 1], "Read,Glob,Grep")
        return subprocess.CompletedProcess(command, 0, json.dumps({"result": BODY, "subtype": "success"}), "")

    def test_generation_saves_custom_path_and_idempotently_skips_repeat(self):
        with patch.object(runner.subprocess, "run", side_effect=self.fake_process) as execute, contextlib.redirect_stdout(io.StringIO()):
            target = runner.run(self.args)
            self.assertEqual(target.read_text().strip(), BODY)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(runner.run(self.args), target)
            self.assertEqual(execute.call_count, 2)
        status = json.loads((self.directory / "data/logs/last_run.json").read_text())
        self.assertTrue(status["ok"])

    def test_model_error_does_not_claim_saved(self):
        def failed(command, **kwargs):
            if command[0] == "lark-cli":
                return self.fake_process(command, **kwargs)
            return subprocess.CompletedProcess(command, 0, json.dumps({"result": BODY, "is_error": True}), "")
        with patch.object(runner.subprocess, "run", side_effect=failed), self.assertRaises(RuntimeError):
            runner.run(self.args)
        self.assertFalse((self.directory / "custom reports" / f"{DAY}.md").exists())
        self.assertFalse(json.loads((self.directory / "data/logs/last_run.json").read_text())["ok"])

    def test_failed_calendar_never_reuses_old_snapshot(self):
        old = self.directory / "data/raw" / str(DAY) / "calendar.json"
        old.parent.mkdir(parents=True)
        old.write_text('{"ok":true,"data":{"events":["old"]}}')
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "failed")), self.assertRaises(RuntimeError):
            runner.run(self.args)
        manifest = next(old.parent.glob("run-*/sources.json"))
        self.assertEqual(json.loads(manifest.read_text())["sources"]["feishu"], "failed")
        self.assertFalse((manifest.parent / "calendar.json").exists())

    def test_email_timeout_degrades_and_keeps_calendar(self):
        self.config["sources"]["email"]["enabled"] = True
        self.path.write_text(json.dumps(self.config))
        def execute(command, **kwargs):
            if "collect_email.py" in " ".join(command):
                raise subprocess.TimeoutExpired(command, 180)
            return self.fake_process(command, **kwargs)
        with patch.object(runner.subprocess, "run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(runner.run(self.args).exists())
        status = json.loads((self.directory / "data/logs/last_run.json").read_text())
        self.assertEqual(status["sources"]["email"], "failed")
        self.assertEqual(status["sources"]["feishu"], "ok")

    def test_disabled_schedule_never_collects(self):
        self.args.scheduled = True
        self.config["schedule"]["enabled"] = False
        self.path.write_text(json.dumps(self.config))
        with patch.object(runner.subprocess, "run") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(runner.run(self.args))
            execute.assert_not_called()

    def test_codex_is_read_only_and_supports_non_git_directory(self):
        raw = self.directory / "raw"
        raw.mkdir()
        def execute(command, **kwargs):
            self.assertIn("--skip-git-repo-check", command)
            self.assertEqual(command[command.index("--sandbox")+1], "read-only")
            Path(command[command.index("--output-last-message")+1]).write_text(BODY)
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(runner.subprocess, "run", side_effect=execute):
            self.assertEqual(runner.generate(self.config, self.path, raw, DAY, "codex"), BODY)

    def test_dst_day_uses_separate_offsets(self):
        start, end = runner.day_bounds(date(2026, 3, 8), "America/New_York")
        self.assertTrue(start.endswith("-05:00"))
        self.assertTrue(end.endswith("-04:00"))

    def test_atomic_save_cannot_overwrite_existing_or_follow_symlink(self):
        output = self.directory / "reports"
        output.mkdir()
        untouched = self.directory / "untouched.md"
        untouched.write_text("original")
        (output / f"{DAY}.md").symlink_to(untouched)
        with self.assertRaises(FileExistsError):
            saver.save_report(BODY, str(output), str(DAY))
        self.assertEqual(untouched.read_text(), "original")
        saver.save_report(BODY, str(output), str(DAY), force=True)
        self.assertEqual(untouched.read_text(), "original")
        with self.assertRaises(ValueError):
            saver.save_report(BODY, str(output), "2026-02-31")


if __name__ == "__main__":
    unittest.main()
