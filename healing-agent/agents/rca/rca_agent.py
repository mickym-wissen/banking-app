"""
LangGraph RCA Agent — autonomous code analysis and PR raising.

Architecture:
  GitHubRCAAgent (this file) orchestrates:
    1. Clone the repo
    2. Run a LangGraph ReAct agent (Groq llama-3.3-70b-versatile) with 5 tools:
         list_tree    — explore repo structure depth-first
         read_file    — read any source file
         search_code  — grep for patterns across the repo
         apply_fix    — apply a code change
         raise_pr     — push branch + open GitHub PR
    3. The agent decides what to read, whether the bug is code-fixable,
       applies the fix, and raises a PR — all autonomously.

The progress_callback is called from inside tools so the SSE stream
shows each step the agent takes in real time.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Annotated, Any, Callable, Optional

import requests
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

log = logging.getLogger(__name__)

# Directories to never descend into
_SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "__pycache__",
    ".mvn", ".gradle", "dist", "out", "bin", "obj",
    ".idea", ".vscode", "coverage", ".nyc_output", ".next",
}

_SYSTEM_PROMPT = """\
You are an autonomous senior software engineer and SRE performing live root-cause analysis.

You have direct access to a cloned GitHub repository via tools. Work through this incident
systematically:

REQUIRED WORKFLOW:
1. Call list_tree("") first — understand the full project structure
2. Call read_file() on the most relevant files (look for class names from the stack trace)
3. Call search_code() to find specific methods, variables, or patterns
4. Decide: can this incident be fixed with a targeted code change?
   - YES → call apply_fix(), then immediately call raise_pr() with a clear PR
   - NO  → stop tool calls and write a detailed RCA in your final message

RULES:
- Read at least 3 files before concluding
- For Java stack traces, trace the exact failing line (ClassName.java:N)
- Only call apply_fix() when you have found the EXACT original_code verbatim in the file
- Only call raise_pr() after apply_fix() has succeeded
- If the issue is infrastructure, DB, config, or external — do NOT try to fix code
- Your final message (when you stop calling tools) becomes the RCA report

INCIDENT BEING ANALYZED:
{incident_block}
"""


class RCAAgent:
    """Autonomous LangGraph + Groq agent that clones, analyzes, and optionally fixes code."""

    def __init__(self, groq_api_key: str, github_token: str) -> None:
        self.groq_api_key = groq_api_key
        self.github_token = github_token

        self._llm = ChatGroq(
            api_key=groq_api_key,
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=4096,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        repo_url: str,
        incident: dict,
        progress: Callable[[str, str], None],
    ) -> dict:
        """
        Clone repo, run the LangGraph agent, return result dict.

        Returns:
            { status, pr_url, pr_number, rca_report, fix_file, fix_desc }
        """
        clone_dir: str | None = None
        try:
            progress("clone", f"Cloning {repo_url} ...")
            clone_dir = self._clone(repo_url)
            progress("clone_done", "Repository cloned — starting autonomous analysis")

            result = self._run_agent(repo_url, incident, clone_dir, progress)
            return result

        except Exception as exc:
            log.error("RCAAgent.analyze failed: %s", exc, exc_info=True)
            raise
        finally:
            if clone_dir and os.path.exists(clone_dir):
                shutil.rmtree(clone_dir, ignore_errors=True)

    # ── Clone ─────────────────────────────────────────────────────────────────

    def _clone(self, repo_url: str) -> str:
        if self.github_token:
            authed_url = re.sub(r"^https://", f"https://{self.github_token}@", repo_url)
        else:
            authed_url = repo_url

        clone_dir = tempfile.mkdtemp(prefix="rca_agent_")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", authed_url, clone_dir],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git clone failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        return clone_dir

    # ── LangGraph agent ───────────────────────────────────────────────────────

    def _run_agent(
        self,
        repo_url: str,
        incident: dict,
        clone_dir: str,
        progress: Callable[[str, str], None],
    ) -> dict:
        # Mutable result bag filled by tool side-effects
        result: dict[str, Any] = {
            "status":    "rca_only",
            "pr_url":    None,
            "pr_number": None,
            "rca_report": "",
            "fix_file":  None,
            "fix_desc":  None,
            "fix_applied": False,
        }

        tools = self._build_tools(clone_dir, repo_url, incident, progress, result)
        agent = create_react_agent(self._llm, tools)

        incident_block = (
            f"Exception type : {incident.get('exception_type') or 'N/A'}\n"
            f"Severity       : {incident.get('severity') or 'N/A'}\n"
            f"Service        : {incident.get('service') or 'N/A'}\n"
            f"Gemini analysis: {incident.get('analysis') or 'N/A'}\n"
            f"Suggested action: {incident.get('suggested_action') or 'N/A'}\n"
            f"Raw log (3000 chars):\n{(incident.get('raw_log') or '')[:3000]}"
        )

        system_msg = SystemMessage(
            content=_SYSTEM_PROMPT.format(incident_block=incident_block)
        )
        human_msg = HumanMessage(
            content=(
                "Start the root-cause analysis now. "
                "Begin by calling list_tree('') to explore the repository structure."
            )
        )

        progress("agent_start", "LangGraph agent started — Groq llama-3.3-70b-versatile")

        try:
            output = agent.invoke(
                {"messages": [system_msg, human_msg]},
                config={"recursion_limit": 40},
            )
        except Exception as exc:
            raise RuntimeError(f"LangGraph agent error: {exc}") from exc

        # Extract the final AI message as the RCA report
        for msg in reversed(output.get("messages", [])):
            if hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
                result["rca_report"] = result["rca_report"] or str(msg.content)
                break

        progress(
            "agent_done",
            f"Analysis complete — status: {result['status']}" +
            (f" | PR #{result['pr_number']}" if result.get("pr_number") else ""),
        )

        return result

    # ── Tool factory ──────────────────────────────────────────────────────────

    def _build_tools(
        self,
        clone_dir: str,
        repo_url: str,
        incident: dict,
        progress: Callable[[str, str], None],
        result: dict,
    ) -> list:
        """
        Build tools as closures so they share access to clone_dir,
        the github_token, and the result dict.
        """
        github_token = self.github_token

        @tool
        def list_tree(subpath: str = "") -> str:
            """
            List the repository directory tree.
            Call with subpath='' for the root, or a relative path like 'src/main/java'.
            Returns a tree view up to depth 4 so you can navigate the project layout.
            """
            progress("explore", f"Exploring directory: /{subpath or ''}")
            base = os.path.join(clone_dir, subpath.replace("/", os.sep)) if subpath else clone_dir
            if not os.path.isdir(base):
                return f"Directory not found: {subpath}"

            lines: list[str] = []

            def _walk(path: str, prefix: str = "", depth: int = 0) -> None:
                if depth > 4:
                    return
                try:
                    entries = sorted(os.scandir(path), key=lambda e: (e.is_file(), e.name))
                except PermissionError:
                    return
                for i, entry in enumerate(entries):
                    if entry.name in _SKIP_DIRS:
                        continue
                    connector = "└── " if i == len(entries) - 1 else "├── "
                    lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
                    if entry.is_dir():
                        ext = "    " if i == len(entries) - 1 else "│   "
                        _walk(entry.path, prefix + ext, depth + 1)

            _walk(base)
            tree = "\n".join(lines)
            return f"/{subpath or ''}:\n{tree}" if tree else f"/{subpath or ''}: (empty)"

        @tool
        def read_file(file_path: str) -> str:
            """
            Read the full contents of a source file from the cloned repository.
            file_path must be relative to the repository root (e.g. 'src/main/java/com/demo/AccountService.java').
            Returns the file content with line numbers prepended.
            """
            progress("read", f"Reading: {file_path}")
            full = os.path.join(clone_dir, file_path.replace("/", os.sep))
            if not os.path.isfile(full):
                return f"File not found: {file_path}"
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                numbered = "".join(f"{i+1:4d}  {l}" for i, l in enumerate(lines))
                if len(numbered) > 20_000:
                    numbered = numbered[:20_000] + "\n... (truncated)"
                return numbered
            except OSError as exc:
                return f"Error reading {file_path}: {exc}"

        @tool
        def search_code(pattern: str, file_extension: str = ".java") -> str:
            """
            Search for a regex pattern in all repository files with the given extension.
            Returns matching lines with their file path and line number.
            Example: search_code('NullPointerException', '.java')
            Example: search_code('def transfer', '.py')
            """
            progress("search", f"Searching '{pattern}' in *{file_extension} files")
            matches: list[str] = []
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                return f"Invalid regex pattern: {exc}"

            for root, dirs, files in os.walk(clone_dir):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fn in files:
                    if not fn.endswith(file_extension):
                        continue
                    full = os.path.join(root, fn)
                    rel  = os.path.relpath(full, clone_dir).replace("\\", "/")
                    try:
                        with open(full, encoding="utf-8", errors="ignore") as f:
                            for i, line in enumerate(f, 1):
                                if compiled.search(line):
                                    matches.append(f"{rel}:{i}: {line.rstrip()}")
                                    if len(matches) >= 60:
                                        break
                    except OSError:
                        pass
                    if len(matches) >= 60:
                        break

            if not matches:
                return f"No matches found for '{pattern}' in *{file_extension} files"
            return "\n".join(matches)

        @tool
        def apply_fix(file_path: str, original_code: str, fixed_code: str) -> str:
            """
            Apply a code fix to a file in the cloned repository.
            original_code MUST exist verbatim in the file — copy it exactly from read_file output (without line numbers).
            fixed_code is the replacement.
            Call raise_pr() immediately after this succeeds.
            """
            progress("fix", f"Applying fix to {file_path} ...")
            full = os.path.join(clone_dir, file_path.replace("/", os.sep))
            if not os.path.isfile(full):
                return f"ERROR: File not found: {file_path}"

            with open(full, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if original_code not in content:
                return (
                    "ERROR: original_code not found verbatim in the file. "
                    "Use read_file() again and copy the exact lines without the line-number prefix."
                )

            new_content = content.replace(original_code, fixed_code, 1)
            with open(full, "w", encoding="utf-8") as f:
                f.write(new_content)

            result["fix_applied"] = True
            result["fix_file"] = file_path
            progress("fix_done", f"Fix applied to {file_path}")
            return f"Fix applied successfully to {file_path}. Now call raise_pr() to push and open the PR."

        @tool
        def raise_pr(pr_title: str, pr_body: str, fix_description: str) -> str:
            """
            Push the fix branch to GitHub and open a Pull Request.
            Call this ONLY after apply_fix() has returned successfully.
            pr_title: short imperative title (max 72 chars)
            pr_body: markdown body with Root Cause, Fix Applied, Testing sections
            fix_description: one-sentence summary of what the fix does
            """
            if not result.get("fix_applied"):
                return "ERROR: Cannot raise PR — apply_fix() has not been called yet or failed."

            progress("pr", f"Pushing branch and opening PR: {pr_title}")

            incident_id = incident.get("id", "auto")
            branch = f"fix/rca-incident-{incident_id}"

            git = ["git", "-C", clone_dir]
            try:
                subprocess.run([*git, "config", "user.email", "rca-agent@selfhealing.ai"], check=True, capture_output=True)
                subprocess.run([*git, "config", "user.name",  "Self-Healing RCA Agent"],  check=True, capture_output=True)
                subprocess.run([*git, "checkout", "-b", branch], check=True, capture_output=True, text=True)
                subprocess.run([*git, "add", "-A"],               check=True, capture_output=True)
                subprocess.run([*git, "commit", "-m", pr_title],  check=True, capture_output=True, text=True)
                subprocess.run([*git, "push", "origin", branch],  check=True, capture_output=True, text=True, timeout=60)
            except subprocess.CalledProcessError as exc:
                err = exc.stderr.strip() if exc.stderr else exc.stdout.strip()
                return f"ERROR during git operations: {err}"

            # Parse owner/repo from URL
            match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?(?:/|$)", repo_url)
            if not match:
                return f"ERROR: Cannot parse GitHub owner/repo from {repo_url}"
            owner_repo = match.group(1)

            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            repo_resp = requests.get(f"https://api.github.com/repos/{owner_repo}", headers=headers, timeout=15)
            default_branch = repo_resp.json().get("default_branch", "master") if repo_resp.ok else "master"

            pr_resp = requests.post(
                f"https://api.github.com/repos/{owner_repo}/pulls",
                headers=headers,
                json={"title": pr_title, "body": pr_body, "head": branch, "base": default_branch},
                timeout=15,
            )
            if not pr_resp.ok:
                err = pr_resp.json().get("message", pr_resp.text[:300])
                return f"ERROR: GitHub PR creation failed ({pr_resp.status_code}): {err}"

            pr = pr_resp.json()
            pr_url    = pr["html_url"]
            pr_number = pr["number"]

            result["status"]     = "pr_raised"
            result["pr_url"]     = pr_url
            result["pr_number"]  = pr_number
            result["fix_desc"]   = fix_description

            progress("pr_done", f"PR #{pr_number} raised: {pr_url}")
            return f"PR #{pr_number} raised successfully: {pr_url}"

        return [list_tree, read_file, search_code, apply_fix, raise_pr]
