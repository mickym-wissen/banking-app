import sys
import logging

# Force UTF-8 on Windows so box-drawing characters print correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config.settings import settings
from db.database import initialize_database
from agents.log_monitor.agent import LogMonitorAgent  # registers itself
from agents.core import AgentRegistry


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.APP_LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    _setup_logging()
    logger = logging.getLogger("main")

    print("\n\033[1m\033[96m" + "=" * 55)
    print("   Self-Healing Agent System  —  Starting")
    print("=" * 55 + "\033[0m\n")

    settings.validate()

    logger.info("Initialising database ...")
    initialize_database()
    logger.info("Database ready. Registered agents: %s", AgentRegistry.list())

    try:
        LogMonitorAgent().run()
    except KeyboardInterrupt:
        print("\n\033[93m[main]\033[0m Stopped.")


if __name__ == "__main__":
    main()
