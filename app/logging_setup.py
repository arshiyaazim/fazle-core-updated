"""Fazle Core — log setup with file rotation (Batch 15.7)."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_initialized = False
_DEFAULT_LOG_DIR = str((Path(__file__).resolve().parents[1] / "logs"))


def setup_logging() -> None:
    global _initialized
    if _initialized:
        return
    log_dir = Path(os.getenv("FAZLE_LOG_DIR", _DEFAULT_LOG_DIR))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "fazle-core.log"

    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    root = logging.getLogger()
    # Avoid double-add on reload
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path):
            _initialized = True
            return
    root.addHandler(fh)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _initialized = True
