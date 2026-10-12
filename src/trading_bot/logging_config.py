"""Logging setup: timestamped output to console AND a rotating file.

Every signal, risk decision, order, and fill flows through here. The file log
is the audit trail you go back to when asking "why did the bot do that?".
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: str | Path = "logs",
    level: int = logging.INFO,
    filename: str = "trading_bot.log",
) -> None:
    """Configure the root logger. Call once at the start of every script.

    Console shows what is happening now; the rotating file (5 MB x 3 backups)
    keeps history without ever filling the disk.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_path / filename, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace (not append to) handlers so calling setup_logging twice --
    # e.g. in tests -- doesn't double every log line.
    root.handlers = [console, file_handler]

    # ib_async logs every incoming tick at INFO; keep it to warnings.
    logging.getLogger("ib_async").setLevel(logging.WARNING)
    # httpx (used by the Gemini SDK) logs every request at INFO -- too chatty.
    logging.getLogger("httpx").setLevel(logging.WARNING)
