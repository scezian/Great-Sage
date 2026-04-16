#!/usr/bin/env python3
"""
debug_artemis.py — Surgical logger for Great Sage / Artemis crashes.
Run instead of great_sage_gui.py. Logs everything to debug_artemis.log
"""

import sys
import os
import signal
import faulthandler
import traceback
import threading
import logging
from pathlib import Path
from datetime import datetime
from functools import wraps

# ── 1. CRASH DUMP — catches segfaults and C-level crashes ─────────────────────
crash_log = open("debug_crash_dump.log", "w")
faulthandler.enable(file=crash_log)          # dumps C stack on SIGSEGV / SIGFPE
faulthandler.dump_traceback_later(           # force-dumps if frozen for 30s
    timeout=30, repeat=True, file=crash_log)

# ── 2. MAIN LOGGER ────────────────────────────────────────────────────────────
LOG_FILE = "debug_artemis.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("GreatSageDebug")
log.info("=" * 70)
log.info(f"DEBUG SESSION STARTED — {datetime.now()}")
log.info(f"Python {sys.version}")
log.info(f"Working dir: {Path.cwd()}")
log.info("=" * 70)

# ── 3. GLOBAL EXCEPTION HOOKS ─────────────────────────────────────────────────
def handle_exception(exc_type, exc_value, exc_tb):
    log.critical("UNHANDLED EXCEPTION ON MAIN THREAD:")
    log.critical("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    crash_log.write("\nUNHANDLED EXCEPTION:\n")
    crash_log.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    crash_log.flush()

sys.excepthook = handle_exception

def handle_thread_exception(args):
    log.critical(f"UNHANDLED EXCEPTION ON THREAD [{args.thread.name}]:")
    log.critical("".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback)))

threading.excepthook = handle_thread_exception

# ── 4. LINE-BY-LINE TRACER ────────────────────────────────────────────────────
# Traces every line executed inside artemis.py and great_sage_*.py
TRACE_MODULES = {"artemis", "great_sage_gui", "great_sage_core"}
_last_thread = {}

def line_tracer(frame, event, arg):
    filename = frame.f_code.co_filename
    module   = Path(filename).stem

    if module not in TRACE_MODULES:
        return line_tracer  # keep tracing but skip logging

    thread_name = threading.current_thread().name
    func  = frame.f_code.co_name
    lineno = frame.f_lineno

    if event == "call":
        log.debug(f"→ CALL   {module}.{func}()  [line {lineno}]")
    elif event == "line":
        try:
            import linecache
            src = linecache.getline(filename, lineno).rstrip()
        except Exception:
            src = "(unavailable)"
        log.debug(f"  LINE   {module}.{func}:{lineno}  {src}")
    elif event == "return":
        log.debug(f"← RETURN {module}.{func}()  → {repr(arg)[:120]}")
    elif event == "exception":
        exc_type, exc_val, _ = arg
        log.error(f"✖ EXCEPTION in {module}.{func}:{lineno} "
                  f"— {exc_type.__name__}: {exc_val}")

    return line_tracer

sys.settrace(line_tracer)
threading.settrace(line_tracer)

# ── 5. Qt SIGNAL MONITOR — patches PyQt6 to log every signal emit ─────────────
def install_qt_signal_monitor():
    try:
        from PyQt6.QtCore import QObject
        # Instead we patch QThread to log start/finish/terminate
        from PyQt6.QtCore import QThread

        original_start = QThread.start
        def patched_start(self, *a, **kw):
            log.info(f"🧵 QTHREAD START — {self.__class__.__name__} "
                     f"id={id(self)}")
            return original_start(self, *a, **kw)
        QThread.start = patched_start

        original_quit = QThread.quit
        def patched_quit(self):
            log.info(f"🧵 QTHREAD QUIT — {self.__class__.__name__} "
                     f"id={id(self)}")
            return original_quit(self)
        QThread.quit = patched_quit

        original_wait = QThread.wait
        def patched_wait(self, *a, **kw):
            log.info(f"🧵 QTHREAD WAIT — {self.__class__.__name__} "
                     f"id={id(self)} timeout={a}")
            result = original_wait(self, *a, **kw)
            log.info(f"🧵 QTHREAD WAIT DONE — {self.__class__.__name__} "
                     f"result={result}")
            return result
        QThread.wait = patched_wait

        log.info("Qt QThread monitor installed.")
    except Exception as e:
        log.warning(f"Could not install Qt monitor: {e}")

install_qt_signal_monitor()

# ── 6. PERIODIC THREAD SNAPSHOT — dumps all live threads every 10 seconds ─────
def thread_watchdog():
    import time
    while True:
        time.sleep(10)
        frames = sys._current_frames()
        log.info(f"── THREAD SNAPSHOT ({len(frames)} threads) ──")
        for tid, frame in frames.items():
            name = "unknown"
            for t in threading.enumerate():
                if t.ident == tid:
                    name = t.name
                    break
            tb_lines = traceback.format_stack(frame)
            log.info(f"  Thread [{name}] tid={tid}:\n"
                     + "".join(tb_lines[-4:]))  # last 4 frames only

watchdog = threading.Thread(target=thread_watchdog, name="Watchdog",
                            daemon=True)
watchdog.start()
log.info("Thread watchdog started (snapshots every 10s).")

# ── 7. BOOT THE ACTUAL APP ────────────────────────────────────────────────────
log.info("Importing and launching great_sage_gui.main() ...")
try:
    import great_sage_gui
    great_sage_gui.main()
except SystemExit as e:
    log.info(f"App exited cleanly with code {e.code}")
except Exception as e:
    log.critical(f"FATAL ERROR LAUNCHING APP: {e}")
    log.critical(traceback.format_exc())
finally:
    faulthandler.cancel_dump_traceback_later()
    crash_log.flush()
    crash_log.close()
    log.info("Debug session ended.")
