"""
LocalFileSource — tails a local log file and yields complete log entries as strings.
Each yielded string is one complete log entry (may span multiple lines for stack traces).
"""
import os
import re
import time
import logging
import threading
from typing import Generator, Optional

log = logging.getLogger(__name__)

_LOG_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

# Healing-agent root: 3 levels up from this file (sources/ → log_monitor/ → agents/ → root)
_AGENT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)


def _resolve(path: str) -> str:
    """Resolve a relative path anchored to the healing-agent root, not the shell cwd."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(_AGENT_ROOT, path))


class LocalFileSource:
    """Yields complete log-entry strings read from a local file in real-time."""

    def __init__(
        self,
        path: Optional[str] = None,
        interval: Optional[float] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        from config.settings import settings
        self.path = _resolve(path or settings.LOG_FILE_PATH)
        self.interval = interval if interval is not None else settings.LOG_CHECK_INTERVAL
        self.stop_event = stop_event

    def stream(self) -> Generator[str, None, None]:
        # Seek to end of file — only yield entries written AFTER monitoring starts.
        # This prevents re-analyzing log lines that are already stored in the DB.
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                position = f.tell()
        except FileNotFoundError:
            position = 0

        pending: list[str] = []
        log.info("LocalFileSource: watching %s from EOF (poll every %.1fs)", self.path, self.interval)

        while not self._stopped():
            try:
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(position)
                    new_content = f.read()
                    position = f.tell()
            except FileNotFoundError:
                log.warning("File not found: %s — retrying in 3s", self.path)
                time.sleep(3)
                continue
            except Exception as exc:
                log.error("Read error on %s: %s", self.path, exc)
                time.sleep(3)
                continue

            if not new_content:
                if pending:
                    yield "\n".join(pending)
                    pending = []
            else:
                all_lines = pending + new_content.splitlines()
                current: list[str] = []
                for line in all_lines:
                    if _LOG_START_RE.match(line):
                        if current:
                            yield "\n".join(current)
                        current = [line]
                    elif current:
                        current.append(line)
                pending = current  # last entry may still be growing

            time.sleep(self.interval)

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()
