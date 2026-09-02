from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from lba.crypto import SecretBox  # noqa: E402
from lba.db import Database  # noqa: E402

TEST_KEY = SecretBox.generate_key()


def temp_db() -> Database:
    handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    handle.close()
    return Database(Path(handle.name))
