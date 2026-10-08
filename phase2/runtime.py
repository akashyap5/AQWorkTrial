"""Best-effort runtime protection for one explicitly requested batch."""
from contextlib import contextmanager
import logging
import os
import shutil
import subprocess
import sys


@contextmanager
def keep_awake():
    """Prevent macOS idle system sleep until the batch reaches its checkpoint.

    This does not keep the display awake or override explicit sleep. The child
    also watches this worker's PID so a killed worker cannot leave an inhibitor
    behind. Other platforms and Macs without caffeinate run normally.
    """
    executable = shutil.which("caffeinate") if sys.platform == "darwin" else None
    if executable is None:
        yield False
        return
    try:
        process = subprocess.Popen(
            [executable, "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        logging.getLogger(__name__).warning("Could not start the macOS idle-sleep inhibitor.")
        yield False
        return
    try:
        yield True
    finally:
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)
        except ProcessLookupError:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
