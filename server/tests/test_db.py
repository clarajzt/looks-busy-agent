import unittest

from .helpers import SecretBox, TEST_KEY, temp_db


class DbTests(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        self.box = SecretBox(TEST_KEY)

    def test_user_and_credential_roundtrip(self):
        self.db.upsert_user("ou_1", "Asia/Shanghai", "21:00")
        self.db.update_user("ou_1", work_scope=["a", "b"], run_time="22:30")
        user = self.db.get_user("ou_1")
        self.assertEqual(user["work_scope"], ["a", "b"])
        self.assertEqual(user["run_time"], "22:30")
        blob = self.box.encrypt({"password": "x"})
        self.db.set_credential("ou_1", "imap", blob, self.box.key_version, {"host": "imap.example.com"})
        cred = self.db.get_credential("ou_1", "imap")
        self.assertEqual(cred["meta"]["host"], "imap.example.com")
        self.assertEqual(self.box.decrypt(cred["ciphertext"]), {"password": "x"})
        self.db.set_credential_status("ou_1", "imap", "needs_reauth", "imap_auth")
        self.assertEqual(self.db.get_credential("ou_1", "imap")["status"], "needs_reauth")
        # re-binding resets status
        self.db.set_credential("ou_1", "imap", blob, self.box.key_version, {})
        self.assertEqual(self.db.get_credential("ou_1", "imap")["status"], "active")

    def test_delete_user_cascades(self):
        self.db.upsert_user("ou_2", "Asia/Shanghai", "21:00")
        self.db.set_credential("ou_2", "imap", b"c", 1, {})
        run_id = self.db.create_run("ou_2", "2026-09-02", "daily", "schedule")
        self.db.save_report(run_id, "ou_2", "2026-09-02", "报告")
        self.assertTrue(self.db.delete_user("ou_2"))
        self.assertIsNone(self.db.get_credential("ou_2", "imap"))
        self.assertIsNone(self.db.latest_report("ou_2"))
        self.assertFalse(self.db.has_daily_run("ou_2", "2026-09-02"))

    def test_daily_run_is_idempotent(self):
        self.db.upsert_user("ou_3", "Asia/Shanghai", "21:00")
        first = self.db.create_run("ou_3", "2026-09-02", "daily", "schedule")
        self.assertIsNotNone(first)
        self.assertIsNone(self.db.create_run("ou_3", "2026-09-02", "daily", "catchup"))
        self.assertIsNotNone(self.db.create_run("ou_3", "2026-09-02", "manual:1", "manual"))
        self.db.finish_run(first, "ok", sources={"calendar": {"state": "ok", "count": 2}})
        self.assertTrue(self.db.has_daily_run("ou_3", "2026-09-02"))

    def test_event_dedupe_and_kv(self):
        self.assertTrue(self.db.mark_event("evt-1"))
        self.assertFalse(self.db.mark_event("evt-1"))
        self.db.kv_set("last_tick_at", "x")
        self.assertEqual(self.db.kv_get("last_tick_at"), "x")


if __name__ == "__main__":
    unittest.main()
