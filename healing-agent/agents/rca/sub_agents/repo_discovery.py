"""
Sub-agent that discovers the GitHub repository for a service name.
Uses Anthropic Claude with a search_github tool.
"""
import json
import logging
import anthropic
from config.settings import settings
from agents.rca.github_tools import search_code_in_repo

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a GitHub repository discovery agent. Your only job is to find the GitHub repository for a given service name within a specific org. Search systematically:
1. Exact name match first (e.g. service "AccountService" → look for repos named "account-service", "AccountService", etc.)
2. Fuzzy name match
3. Code search in pyproject.toml, package.json, build.gradle, pom.xml for the service name

Return ONLY a JSON object: {"github_org": "...", "github_repo": "..."} on success, or {"error": "no_match", "candidates": [...]} if not found. No other text."""


class RepoDiscoverySubAgent:
    def discover(self, service_name: str) -> dict:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        tools = [
            {
                "name": "search_github",
                "description": "Search for repos or code in the GitHub org",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "search_type": {
                            "type": "string",
                            "enum": ["repos", "code"],
                        },
                    },
                    "required": ["query", "search_type"],
                },
            }
        ]

        messages = [
            {
                "role": "user",
                "content": (
                    f"Find the GitHub repository for service: {service_name}\n"
                    f"Org: {settings.GITHUB_ORG}"
                ),
            }
        ]

        for _ in range(5):
            response = client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        try:
                            return json.loads(block.text)
                        except Exception:
                            pass
                return {"error": "no_match", "candidates": []}

            tool_results = []
            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "tool_use":
                    result = search_code_in_repo(
                        settings.GITHUB_ORG, "", block.input.get("query", "")
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        return {"error": "no_match", "candidates": []}
