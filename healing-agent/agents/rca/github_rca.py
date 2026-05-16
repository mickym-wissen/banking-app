"""
GitHub RCA Agent — clones a GitHub repo, analyzes the code with Gemini 2.5 Flash
against a live incident, and either raises a PR with a fix or returns a detailed
RCA report when a code fix is not possible.

Flow:
  1. Clone repo (depth=1) authenticated via GITHUB_TOKEN
  2. Collect and prioritize source files (exclude binaries / build output)
  3. Ask Gemini 2.5 Flash: can this incident be fixed with a code change?
  4a. If yes → apply the fix, push a new branch, raise a PR via GitHub API
  4b. If no  → return the RCA report
  5. Emit progress updates via callback throughout so the UI can stream them live
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable

import requests
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

log = logging.getLogger(__name__)

# Maximum characters of repo content sent to Gemini
_MAX_REPO_CHARS = 80_000
# Maximum files to include
_MAX_FILES = 40
# Extensions to include
_SRC_EXTS = {
    ".java", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".kt", ".go", ".cs", ".rb", ".php", ".rs",
    ".yml", ".yaml", ".properties", ".xml", ".json",
    ".sql", ".sh", ".env.example",
}
# Directories to skip entirely
_SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "__pycache__",
    ".mvn", ".gradle", "dist", "out", "bin", "obj",
    ".idea", ".vscode", "coverage", ".nyc_output",
}

_SYSTEM_PROMPT = """You are a senior software engineer performing root-cause analysis.
Given an incident from a production system and the relevant repository source code,
you must determine whether the incident can be resolved with a specific code change.

Respond with ONLY a valid JSON object — no markdown fences, no explanation outside the JSON.

Schema:
{
  "can_fix": true | false,
  "rca_report": "<detailed root-cause analysis, 3-5 sentences>",
  "fix": {
    "file": "<relative file path in the repo>",
    "description": "<what changed and why this fixes the problem>",
    "original_code": "<exact verbatim code block that must be replaced — must exist in the file>",
    "fixed_code": "<replacement code block>",
    "pr_title": "<imperative title, max 72 chars, e.g. fix: resolve NPE in AccountService>",
    "pr_body": "<markdown PR description with Root Cause, Fix Applied, and Testing sections>"
  }
}

Set can_fix=false and fix=null when:
- The issue is infrastructure, configuration, or data-related (not a code bug)
- You cannot identify the specific file and line with confidence
- The fix would require compilation or runtime validation you cannot perform

Set can_fix=true only when you can identify the EXACT lines to change and are confident
the replacement will compile and resolve the issue."""


class GitHubRCAAgent:
    """Analyzes a production incident against a GitHub repository."""

    def __init__(self, github_token: str, gemini_api_key: str) -> None:
        self.github_token = github_token
        self._llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=gemini_api_key,
            temperature=0,
            max_output_tokens=4096,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        repo_url: str,
        incident: dict,
        progress: Callable[[str, str], None],
    ) -> dict:
        """
        Main entry point.

        Returns:
            {
              "status":      "pr_raised" | "rca_only",
              "pr_url":      str | None,
              "pr_number":   int | None,
              "rca_report":  str,
              "fix_file":    str | None,
              "fix_desc":    str | None,
            }
        """
        clone_dir: str | None = None
        try:
            # 1. Clone
            progress("clone", f"Cloning {repo_url} ...")
            clone_dir = self._clone(repo_url)
            progress("clone_done", "Repository cloned successfully")

            # 2. Read source files
            progress("read", "Scanning repository structure ...")
            files = self._collect_files(clone_dir, incident)
            progress("read_done", f"Collected {len(files)} source files for analysis")

            # 3. Gemini analysis
            progress("analyze", "Sending to Gemini 2.5 Flash for root-cause analysis ...")
            result = self._gemini_analyze(incident, files)
            progress("analyze_done", "Analysis complete")

            if not result.get("can_fix"):
                progress("result", "No code fix identified — generating RCA report")
                return {
                    "status":     "rca_only",
                    "pr_url":     None,
                    "pr_number":  None,
                    "rca_report": result.get("rca_report", ""),
                    "fix_file":   None,
                    "fix_desc":   None,
                }

            fix = result["fix"]
            progress("fix", f"Applying fix to {fix['file']} ...")
            self._apply_fix(clone_dir, fix)
            progress("fix_done", f"Fix applied to {fix['file']}")

            progress("pr", "Pushing branch and opening pull request ...")
            pr_url, pr_number = self._raise_pr(repo_url, clone_dir, fix, incident)
            progress("pr_done", f"PR #{pr_number} opened: {pr_url}")

            return {
                "status":     "pr_raised",
                "pr_url":     pr_url,
                "pr_number":  pr_number,
                "rca_report": result.get("rca_report", ""),
                "fix_file":   fix["file"],
                "fix_desc":   fix.get("description", ""),
            }

        except Exception as exc:
            log.error("GitHubRCAAgent.analyze failed: %s", exc, exc_info=True)
            raise
        finally:
            if clone_dir and os.path.exists(clone_dir):
                shutil.rmtree(clone_dir, ignore_errors=True)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _clone(self, repo_url: str) -> str:
        """Clone repo with PAT auth into a temp directory."""
        # Inject token: https://github.com/owner/repo → https://TOKEN@github.com/owner/repo
        if self.github_token:
            authed_url = re.sub(r"^https://", f"https://{self.github_token}@", repo_url)
        else:
            authed_url = repo_url

        clone_dir = tempfile.mkdtemp(prefix="rca_clone_")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", authed_url, clone_dir],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git clone failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        return clone_dir

    def _collect_files(self, clone_dir: str, incident: dict) -> dict[str, str]:
        """
        Return {relative_path: content} for the most relevant source files.

        Priority order:
          1. Files named in the exception stack trace
          2. Other source files (Java first, then rest)
          3. Config files (yml/yaml/properties)
        """
        # Extract file name hints from the raw log stack trace
        raw_log = incident.get("raw_log", "") or ""
        stack_files: set[str] = set(re.findall(r'(\w+\.java)', raw_log))

        priority: list[tuple[int, str, str]] = []  # (priority, rel_path, content)

        for root, dirs, filenames in os.walk(clone_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _SRC_EXTS:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, clone_dir).replace("\\", "/")
                try:
                    size = os.path.getsize(full)
                    if size > 100_000:
                        continue
                    with open(full, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except OSError:
                    continue

                if fn in stack_files:
                    prio = 0
                elif ext == ".java" and "test" not in rel.lower():
                    prio = 1
                elif ext == ".java":
                    prio = 2
                elif ext in (".yml", ".yaml", ".properties"):
                    prio = 3
                else:
                    prio = 4

                priority.append((prio, rel, content))

        priority.sort(key=lambda x: x[0])

        files: dict[str, str] = {}
        total_chars = 0
        for _, rel, content in priority:
            if len(files) >= _MAX_FILES:
                break
            if total_chars + len(content) > _MAX_REPO_CHARS:
                break
            files[rel] = content
            total_chars += len(content)

        return files

    def _gemini_analyze(self, incident: dict, files: dict[str, str]) -> dict:
        """Ask Gemini 2.5 Flash to analyze the incident against the repo code."""
        files_block = "\n\n".join(
            f"=== {path} ===\n{content}" for path, content in files.items()
        )

        human_content = f"""INCIDENT DETAILS:
- Exception type : {incident.get('exception_type') or 'N/A'}
- Severity       : {incident.get('severity') or 'N/A'}
- Service        : {incident.get('service') or 'N/A'}
- Gemini analysis: {incident.get('analysis') or 'N/A'}
- Suggested action: {incident.get('suggested_action') or 'N/A'}
- Raw log (first 3000 chars):
{(incident.get('raw_log') or '')[:3000]}

REPOSITORY SOURCE FILES:
{files_block}"""

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=human_content),
        ]

        raw = self._llm.invoke(messages).content.strip()

        # Strip markdown fences if Gemini adds them
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Gemini returned non-JSON, retrying with stricter prompt")
            # Retry once with a stricter instruction
            retry_msg = HumanMessage(
                content="Your previous response was not valid JSON. "
                        "Return ONLY the JSON object, no other text."
            )
            raw2 = self._llm.invoke([*messages, retry_msg]).content.strip()
            if raw2.startswith("```"):
                raw2 = raw2.split("```")[1].lstrip("json").strip()
            try:
                return json.loads(raw2)
            except json.JSONDecodeError:
                return {
                    "can_fix":    False,
                    "rca_report": raw2 or raw,
                    "fix":        None,
                }

    def _apply_fix(self, clone_dir: str, fix: dict) -> None:
        """Apply the fix by replacing the original_code block in the target file."""
        file_path = os.path.join(clone_dir, fix["file"].replace("/", os.sep))
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found in cloned repo: {fix['file']}")

        with open(file_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        original = fix.get("original_code", "")
        if original and original not in content:
            raise ValueError(
                f"original_code block not found verbatim in {fix['file']}. "
                "The model may have hallucinated the code."
            )

        new_content = content.replace(original, fix.get("fixed_code", ""), 1) if original else content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    def _raise_pr(
        self,
        repo_url: str,
        clone_dir: str,
        fix: dict,
        incident: dict,
    ) -> tuple[str, int]:
        """Push a new branch with the fix and open a PR via the GitHub API."""
        incident_id = incident.get("id", "auto")
        branch = f"fix/rca-incident-{incident_id}"
        pr_title = fix.get("pr_title", "fix: apply RCA-generated code fix")
        pr_body = fix.get("pr_body", fix.get("description", "Auto-generated fix by Self-Healing RCA Agent."))

        # Configure git identity for the commit
        git = ["git", "-C", clone_dir]
        subprocess.run([*git, "config", "user.email", "rca-agent@selfhealing.ai"], check=True, capture_output=True)
        subprocess.run([*git, "config", "user.name", "Self-Healing RCA Agent"], check=True, capture_output=True)
        subprocess.run([*git, "checkout", "-b", branch], check=True, capture_output=True, text=True)
        subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-m", pr_title], check=True, capture_output=True, text=True)

        # Push (the remote origin already has the token-auth URL from clone)
        try:
            subprocess.run(
                [*git, "push", "origin", branch],
                check=True, capture_output=True, text=True, timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git push failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc

        # Parse owner/repo from URL
        match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?(?:/|$)", repo_url)
        if not match:
            raise ValueError(f"Cannot parse GitHub owner/repo from URL: {repo_url}")
        owner_repo = match.group(1)

        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Determine default branch
        repo_info = requests.get(
            f"https://api.github.com/repos/{owner_repo}",
            headers=headers, timeout=15,
        ).json()
        default_branch = repo_info.get("default_branch", "master")

        pr_resp = requests.post(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            headers=headers,
            json={
                "title": pr_title,
                "body":  pr_body,
                "head":  branch,
                "base":  default_branch,
            },
            timeout=15,
        )
        if not pr_resp.ok:
            raise RuntimeError(
                f"GitHub PR creation failed ({pr_resp.status_code}): "
                f"{pr_resp.json().get('message', pr_resp.text[:300])}"
            )

        pr = pr_resp.json()
        return pr["html_url"], pr["number"]
