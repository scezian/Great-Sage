"""
gs_logger.py — Great Sage Logging System
=========================================

Daily rotating log files in ~/Documents/great sage/logs/
Named by date: 2026-03-20.log
Files older than 30 days are deleted on startup.

Usage anywhere in the app:
    from gs_logger import log
    log.info("Chapter loaded", book="Sage of Humanity", chapter=216)
    log.warning("Slow fetch", url=url, elapsed=3.2)
    log.error("Download failed", book=name, exc=e)
    log.debug("IPC response", data=resp)

    # Log an exception with full traceback:
    try:
        ...
    except Exception as e:
        log.exc("What was happening when this blew up", e)

Log levels (all written to file, filtered by level):
    DEBUG   — verbose internal state, IPC responses, timer ticks
    INFO    — normal events: startup, navigation, chapter loads, downloads
    WARNING — recoverable issues: slow fetches, fallbacks, missing files
    ERROR   — failures: exceptions, broken downloads, API errors
    CRITICAL— app-level failures: startup crash, data corruption
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
LOG_DIR      = Path.home() / "Documents" / "Great-Sage" / "logs"
MAX_AGE_DAYS = 30
LOG_LEVEL    = logging.DEBUG   # log everything


# ── Custom formatter ───────────────────────────────────────────────────────────

class _GsFormatter(logging.Formatter):
    """
    Produces structured, readable log lines.

    Format:
        2026-03-20 11:33:05.421  INFO     [legion      ]  Chapter loaded  book=Sage of Humanity  ch=216
    """

    LEVEL_LABELS = {
        logging.DEBUG:    "DEBUG   ",
        logging.INFO:     "INFO    ",
        logging.WARNING:  "WARNING ",
        logging.ERROR:    "ERROR   ",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts     = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        level  = self.LEVEL_LABELS.get(record.levelno, record.levelname.ljust(8))
        src    = f"[{record.name:<12}]"
        msg    = record.getMessage()

        # Extra key=value pairs passed as `extra`
        extras = ""
        skip = {"name","msg","args","levelname","levelno","pathname","filename",
                "module","exc_info","exc_text","stack_info","lineno","funcName",
                "created","msecs","relativeCreated","thread","threadName",
                "processName","process","message","taskName"}
        for k, v in record.__dict__.items():
            if k not in skip and not k.startswith("_"):
                extras += f"  {k}={v}"

        line = f"{ts}  {level}  {src}  {msg}{extras}"

        # Append traceback if present
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        elif hasattr(record, "traceback_str") and record.traceback_str:
            line += "\n" + record.traceback_str

        return line


# ── Daily file handler ─────────────────────────────────────────────────────────

class _DailyFileHandler(logging.FileHandler):
    """
    Writes to today's date file.  Automatically rotates when the date changes
    mid-session (e.g. app runs past midnight).
    """

    def __init__(self, log_dir: Path):
        self._log_dir  = log_dir
        self._log_date = datetime.now().date()
        path = self._path_for_date(self._log_date)
        super().__init__(str(path), mode="a", encoding="utf-8", delay=False)

    def _path_for_date(self, date) -> Path:
        return self._log_dir / f"{date.isoformat()}.log"

    def emit(self, record: logging.LogRecord):
        today = datetime.now().date()
        if today != self._log_date:
            # Date changed — rotate to new file
            self.close()
            self._log_date = today
            self.baseFilename = str(self._path_for_date(today))
            self.stream = self._open()
        super().emit(record)


# ── Cleanup ────────────────────────────────────────────────────────────────────

def _cleanup_old_logs(log_dir: Path, max_age_days: int = MAX_AGE_DAYS):
    """Delete log files older than max_age_days."""
    cutoff = datetime.now().date() - timedelta(days=max_age_days)
    deleted = 0
    for path in log_dir.glob("*.log"):
        try:
            # Filename is the date e.g. 2026-02-01.log
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            if file_date < cutoff:
                path.unlink()
                deleted += 1
        except (ValueError, OSError):
            pass
    return deleted


# ── Logger proxy ───────────────────────────────────────────────────────────────

class GsLogger:
    """
    Thin wrapper around Python's logging module.
    Provides .debug/.info/.warning/.error/.critical/.exc methods
    with keyword-argument support for structured extras.
    """

    def __init__(self, name: str = "great_sage"):
        self._logger = logging.getLogger(name)

    # Keys that are reserved by Python's LogRecord — passing these as extra
    # raises a KeyError in Python 3.14+. We prefix them with gs_ automatically.
    _RESERVED = frozenset({
        "args","created","exc_info","exc_text","filename","funcName",
        "levelname","levelno","lineno","module","msecs","msg","name",
        "pathname","process","processName","relativeCreated","stack_info",
        "taskName","thread","threadName",
    })

    def _log(self, level: int, msg: str, **kwargs):
        exc_info = kwargs.pop("exc_info", None)
        tb_str   = kwargs.pop("traceback_str", "")
        # Rename any key that would clash with a reserved LogRecord field
        safe = {}
        for k, v in kwargs.items():
            safe[f"gs_{k}" if k in self._RESERVED else k] = v
        if tb_str:
            safe["traceback_str"] = tb_str
        self._logger.log(level, msg, extra=safe, exc_info=exc_info)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)

    def exc(self, msg: str, exception: BaseException, **kwargs):
        """Log an exception with its full traceback."""
        tb = traceback.format_exc()
        self._log(
            logging.ERROR, msg,
            exc_type=type(exception).__name__,
            exc_msg=str(exception),
            traceback_str=tb,
            **kwargs,
        )

    def get_logger(self, name: str) -> "GsLogger":
        """Get a child logger for a specific module."""
        child = GsLogger(f"great_sage.{name}")
        child._logger.parent = self._logger
        return child


# ── Module-level child loggers (one per app module) ───────────────────────────

class _Loggers:
    """Namespace of per-module loggers. Access via log.legion, log.sage etc."""
    def __init__(self, root: GsLogger):
        self.root     = root
        self.legion   = root.get_logger("legion")
        self.matrix   = root.get_logger("matrix")
        self.sage     = root.get_logger("sage")
        self.catalogue= root.get_logger("catalogue")
        self.plugins  = root.get_logger("plugins")
        self.plugin   = self.plugins   # alias
        self.mpv      = root.get_logger("mpv")
        self.network  = root.get_logger("network")
        self.sync     = root.get_logger("sync")
        self.ui       = root.get_logger("ui")
        self.flask    = root.get_logger("flask")

    # Shorthand: log.info(...) delegates to root
    def debug(self, *a, **kw):    self.root.debug(*a, **kw)
    def info(self, *a, **kw):     self.root.info(*a, **kw)
    def warning(self, *a, **kw):  self.root.warning(*a, **kw)
    def error(self, *a, **kw):    self.root.error(*a, **kw)
    def critical(self, *a, **kw): self.root.critical(*a, **kw)
    def exc(self, *a, **kw):      self.root.exc(*a, **kw)


# ── Init ───────────────────────────────────────────────────────────────────────

def _init() -> _Loggers:
    """
    Set up logging on module import.
    Called once automatically — import gs_logger to activate.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("great_sage")
    root_logger.setLevel(LOG_LEVEL)

    # Avoid adding duplicate handlers if re-imported
    if root_logger.handlers:
        return _Loggers(GsLogger("great_sage"))

    fmt = _GsFormatter()

    # ── File handler (daily rotating) ─────────────────────────────────────────
    file_handler = _DailyFileHandler(LOG_DIR)
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # ── Console handler (WARNING+ only, so terminal isn't spammed) ────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    # Prevent log records bubbling up to the root Python logger
    root_logger.propagate = False

    # ── Redirect Python warnings to our logger ─────────────────────────────────
    logging.captureWarnings(True)
    warnings_logger = logging.getLogger("py.warnings")
    warnings_logger.addHandler(file_handler)

    # ── Clean up old log files ────────────────────────────────────────────────
    deleted = _cleanup_old_logs(LOG_DIR)

    # ── Also capture unhandled exceptions ─────────────────────────────────────
    _orig_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            logging.getLogger("great_sage").critical(
                "Unhandled exception — app crashed",
                extra={
                    "exc_type": exc_type.__name__,
                    "exc_msg":  str(exc_value),
                    "traceback_str": tb_str,
                }
            )
        _orig_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    gs = GsLogger("great_sage")
    loggers = _Loggers(gs)

    # First log entry
    gs.info(
        "Great Sage started",
        python=sys.version.split()[0],
        platform=sys.platform,
        log_dir=str(LOG_DIR),
        old_logs_deleted=deleted,
    )

    return loggers


# ── Public singleton ───────────────────────────────────────────────────────────
log = _init()
