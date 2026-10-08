"""Repair an invalid optional TLS debug path in this process only."""
import os
from pathlib import Path


def prepare_runtime():
    keylog = os.environ.get("SSLKEYLOGFILE")
    if keylog and not Path(keylog).parent.is_dir():
        os.environ.pop("SSLKEYLOGFILE", None)
        return [{"variable": "SSLKEYLOGFILE", "reason": "parent_directory_missing"}]
    return []
