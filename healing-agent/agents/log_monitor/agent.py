import logging
from typing import Union

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

logger = logging.getLogger(__name__)


@AgentRegistry.register
class LogMonitorAgent(BaseAgent):
    """
    Processes WARN/ERROR log entries through Gemini → MySQL.
    Supports two log sources:
      - LocalFileSource  : tails a local log file
      - DatadogSource    : polls the Datadog Logs v2 API
    The source is chosen via LOG_SOURCE in .env ("local" | "datadog"),
    or injected directly via the constructor.
    """

    name = "log_monitor"
    description = "Monitors application logs and stores WARNING/ERROR incidents to MySQL."

    def __init__(self, source=None) -> None:
        self._graph  = self.build_graph()
        self._source = source  # injected or resolved in run()

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

    def _resolve_source(self):
        """Pick the right source based on settings if not already set."""
        from config.settings import settings
        if settings.LOG_SOURCE == "datadog":
            from agents.log_monitor.sources.datadog import DatadogSource
            return DatadogSource()
        from agents.log_monitor.sources.local import LocalFileSource
        return LocalFileSource()

    def run(self) -> None:
        source = self._source or self._resolve_source()
        src_name = type(source).__name__

        print(
            f"\n\033[96m[LogMonitorAgent]\033[0m Source: \033[93m{src_name}\033[0m\n",
            flush=True,
        )

        for entry in source.stream():
            try:
                self._graph.invoke({
                    "raw_log_entry":  entry,
                    "parsed_entry":   None,
                    "analysis":       None,
                    "db_incident_id": None,
                    "should_skip":    False,
                    "error":          None,
                })
            except Exception as exc:
                logger.error("Graph invoke error: %s", exc, exc_info=True)
