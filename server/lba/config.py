"""Settings from environment (LBA_*). Fail fast with actionable messages."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

RUN_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前: {raw!r}") from exc


@dataclass
class Settings:
    lark_app_id: str = ""
    lark_app_secret: str = ""
    public_base_url: str = ""
    http_port: int = 8080
    db_path: Path = Path("./data/lba.sqlite3")
    master_key: str = ""
    master_key_previous: str = ""
    llm_format: str = "openai"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_auth: str = "bearer"
    llm_max_tokens: int = 1500
    default_tz: str = "Asia/Shanghai"
    default_run_time: str = "21:00"
    catchup_window_min: int = 180
    skip_weekends: bool = True
    holidays_file: Optional[Path] = None
    report_retention_days: int = 14
    keepalive_days: int = 1
    denylist_file: Optional[Path] = None
    admin_open_ids: List[str] = field(default_factory=list)
    log_level: str = "INFO"
    skill_dir: Path = Path("../skills/looks-busy-agent")

    @classmethod
    def from_env(cls) -> "Settings":
        master_key = _env("LBA_MASTER_KEY", "")
        key_file = _env("LBA_MASTER_KEY_FILE")
        if not master_key and key_file:
            path = Path(key_file)
            if path.is_file():
                master_key = path.read_text(encoding="utf-8").strip()
        settings = cls(
            lark_app_id=_env("LBA_LARK_APP_ID", "") or "",
            lark_app_secret=_env("LBA_LARK_APP_SECRET", "") or "",
            public_base_url=(_env("LBA_PUBLIC_BASE_URL", "") or "").rstrip("/"),
            http_port=_int("LBA_HTTP_PORT", 8080),
            db_path=Path(_env("LBA_DB_PATH", "./data/lba.sqlite3") or "./data/lba.sqlite3"),
            master_key=master_key or "",
            master_key_previous=_env("LBA_MASTER_KEY_PREVIOUS", "") or "",
            llm_format=(_env("LBA_LLM_FORMAT", "openai") or "openai").lower(),
            llm_base_url=(_env("LBA_LLM_BASE_URL", "") or "").rstrip("/"),
            llm_model=_env("LBA_LLM_MODEL", "") or "",
            llm_api_key=_env("LBA_LLM_API_KEY", "") or "",
            llm_auth=(_env("LBA_LLM_AUTH", "bearer") or "bearer").lower(),
            llm_max_tokens=_int("LBA_LLM_MAX_TOKENS", 1500),
            default_tz=_env("LBA_DEFAULT_TZ", "Asia/Shanghai") or "Asia/Shanghai",
            default_run_time=_env("LBA_DEFAULT_RUN_TIME", "21:00") or "21:00",
            catchup_window_min=_int("LBA_CATCHUP_WINDOW_MIN", 180),
            skip_weekends=(_env("LBA_SKIP_WEEKENDS", "1") or "1") not in ("0", "false", "no"),
            holidays_file=Path(_env("LBA_HOLIDAYS_FILE")) if _env("LBA_HOLIDAYS_FILE") else None,
            report_retention_days=_int("LBA_REPORT_RETENTION_DAYS", 14),
            keepalive_days=_int("LBA_KEEPALIVE_DAYS", 1),
            denylist_file=Path(_env("LBA_DENYLIST_FILE")) if _env("LBA_DENYLIST_FILE") else None,
            admin_open_ids=[item.strip() for item in (_env("LBA_ADMIN_OPEN_IDS", "") or "").split(",") if item.strip()],
            log_level=(_env("LBA_LOG_LEVEL", "INFO") or "INFO").upper(),
            skill_dir=Path(_env("LBA_SKILL_DIR", "../skills/looks-busy-agent") or "../skills/looks-busy-agent"),
        )
        return settings

    # --- validation -------------------------------------------------------
    def validate(self, *, need_lark: bool = True, need_llm: bool = False, need_key: bool = True) -> List[str]:
        problems: List[str] = []
        if need_lark:
            if not self.lark_app_id.startswith("cli_"):
                problems.append("LBA_LARK_APP_ID 缺失或格式不对（应以 cli_ 开头）")
            if not self.lark_app_secret:
                problems.append("LBA_LARK_APP_SECRET 缺失")
            if not self.public_base_url.startswith("https://"):
                problems.append("LBA_PUBLIC_BASE_URL 必须是 https:// 开头（OAuth 回调与邮箱表单需要）")
        if need_key and not self.master_key:
            problems.append("主密钥缺失：设置 LBA_MASTER_KEY_FILE（docker secret）或 LBA_MASTER_KEY；生成用 python -m lba gen-key")
        if need_llm:
            if self.llm_format not in ("openai", "anthropic", "fake"):
                problems.append(f"LBA_LLM_FORMAT 只能是 openai|anthropic|fake，当前 {self.llm_format!r}")
            if self.llm_format != "fake" and not (self.llm_base_url and self.llm_model and self.llm_api_key):
                problems.append("LLM 配置不完整：LBA_LLM_BASE_URL / LBA_LLM_MODEL / LBA_LLM_API_KEY")
        if not RUN_TIME_RE.match(self.default_run_time):
            problems.append(f"LBA_DEFAULT_RUN_TIME 需为 HH:MM，当前 {self.default_run_time!r}")
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(self.default_tz)
        except Exception:
            problems.append(f"LBA_DEFAULT_TZ 无效: {self.default_tz!r}")
        if not (self.skill_dir / "references" / "report-policy.md").is_file():
            problems.append(f"LBA_SKILL_DIR 下找不到 references/report-policy.md: {self.skill_dir}")
        return problems

    @property
    def is_token_plan(self) -> bool:
        """Aliyun Token Plan is contractually limited to AI-coding-tool use; flag it."""
        return "token-plan." in self.llm_base_url

    @property
    def policy_path(self) -> Path:
        return self.skill_dir / "references" / "report-policy.md"

    @property
    def collector_dir(self) -> Path:
        return self.skill_dir / "scripts"
