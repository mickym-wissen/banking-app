import logging
import re
import time
from typing import List

from langgraph.graph import END, START, StateGraph

from agents.core import AgentRegistry, BaseAgent
from agents.log_monitor.nodes import (
    LogMonitorState,
    analyze_with_gemini,
    parse_log_entry,
    route_after_parse,
    send_alert,
    store_to_db,
)
from config.settings import settings

logger = logging.getLogger(__name__)
_LOG_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


@AgentRegistry.register
class LogMonitorAgent(BaseAgent):
    """Tails the banking log file and processes WARN/ERROR entries through Gemini → MySQL."""

    name = "log_monitor"
    description = "Monitors application logs and stores WARNING/ERROR incidents to MySQL."

    def __init__(self) -> None:
        self._graph = self.build_graph()
        self._position: int = 0
        self._pending: List[str] = []

    def build_graph(self):
        g = StateGraph(LogMonitorState)
        g.add_node("parse",   parse_log_entry)
        g.add_node("analyze", analyze_with_gemini)
        g.add_node("store",   store_to_db)
        g.add_node("alert",   send_alert)
        g.add_edge(START, "parse")
        g.add_conditional_edges("parse", route_after_parse, {"skip": END, "continue": "analyze"})
        g.add_edge("analyze", "store")
        g.add_edge("store",   "alert")
        g.add_edge("alert",   END)
        return g.compile()

    def _read_new_entries(self) -> List[str]:
        try:
            with open(settings.LOG_FILE_PATH, "r", encoding="utf-8") as f:
                f.seek(self._position)
                new_content = f.read()
                self._position = f.tell()
        except FileNotFoundError:
            return []

        if not new_content:
            if self._pending:
                entry = "\n".join(self._pending)
                self._pending = []
                return [entry]
            return []

        complete, current = [], []
        for line in (self._pending + new_content.splitlines()):
            if _LOG_START.match(line):
                if current:
                    complete.append("\n".join(current))
                current = [line]
            elif current:
                current.append(line)

        self._pending = current  # last entry may still be growing
        return complete

    def run(self) -> None:
        print(f"\n\033[96m[LogMonitorAgent]\033[0m Watching \033[93m{settings.LOG_FILE_PATH}\033[0m "
              f"— every {settings.LOG_CHECK_INTERVAL}s (scanning from beginning)\n", flush=True)
        while True:
            try:
                for entry in self._read_new_entries():
                    self._graph.invoke({
                        "raw_log_entry":  entry,
                        "parsed_entry":   None,
                        "analysis":       None,
                        "db_incident_id": None,
                        "should_skip":    False,
                        "error":          None,
                    })
            except Exception as exc:
                logger.error("Monitor loop error: %s", exc, exc_info=True)
            time.sleep(settings.LOG_CHECK_INTERVAL)
