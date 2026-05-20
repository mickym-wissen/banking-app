import logging
from github import Github, GithubException
from config.settings import settings

logger = logging.getLogger(__name__)
_gh = None


def _client() -> Github:
    global _gh
    if _gh is None:
        _gh = Github(settings.GITHUB_PAT)
    return _gh


def get_repo_file(org: str, repo: str, path: str, ref: str = "main") -> dict:
    """Fetch file content from GitHub at a specific ref (branch or commit SHA)."""
    try:
        r = _client().get_repo(f"{org}/{repo}")
        f = r.get_contents(path, ref=ref)
        return {"path": path, "content": f.decoded_content.decode("utf-8"), "sha": f.sha}
    except GithubException as e:
        logger.warning("get_repo_file failed: %s/%s/%s@%s — %s", org, repo, path, ref, e)
        return {"error": str(e), "path": path}


def list_repo_files(org: str, repo: str, directory: str = "", ref: str = "main") -> dict:
    """List files in a directory (flat, not recursive)."""
    try:
        r = _client().get_repo(f"{org}/{repo}")
        contents = r.get_contents(directory or "", ref=ref)
        if not isinstance(contents, list):
            contents = [contents]
        files = [{"path": c.path, "type": c.type, "size": c.size} for c in contents]
        return {"directory": directory or "/", "files": files}
    except GithubException as e:
        logger.warning("list_repo_files failed: %s/%s/%s — %s", org, repo, directory, e)
        return {"error": str(e)}


def search_code_in_repo(org: str, repo: str, query: str) -> dict:
    """Search for a string or symbol within a repo (or across an org when repo='')."""
    try:
        full_query = f"{query} repo:{org}/{repo}" if repo else f"{query} org:{org}"
        results = _client().search_code(full_query)
        items = [{"path": r.path, "url": r.html_url} for r in list(results)[:10]]
        return {"query": query, "results": items}
    except GithubException as e:
        logger.warning("search_code_in_repo failed: %s — %s", query, e)
        return {"error": str(e), "results": []}


def get_commit_diff(org: str, repo: str, commit_sha: str) -> dict:
    """Get the diff for a specific commit."""
    try:
        r = _client().get_repo(f"{org}/{repo}")
        commit = r.get_commit(commit_sha)
        files = []
        for f in commit.files:
            files.append({
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch or "",
            })
        return {
            "sha": commit_sha,
            "message": commit.commit.message,
            "author": commit.commit.author.name,
            "date": commit.commit.author.date.isoformat(),
            "files": files,
        }
    except GithubException as e:
        logger.warning("get_commit_diff failed: %s@%s — %s", repo, commit_sha, e)
        return {"error": str(e)}


def get_commits_since(org: str, repo: str, branch: str, since: str) -> dict:
    """List commits on a branch since a given ISO timestamp."""
    try:
        from datetime import datetime
        r = _client().get_repo(f"{org}/{repo}")
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        commits = r.get_commits(sha=branch, since=since_dt)
        items = [
            {
                "sha": c.sha,
                "message": c.commit.message,
                "author": c.commit.author.name,
                "date": c.commit.author.date.isoformat(),
            }
            for c in list(commits)[:20]
        ]
        return {"branch": branch, "commits": items}
    except GithubException as e:
        logger.warning("get_commits_since failed: %s/%s — %s", repo, branch, e)
        return {"error": str(e), "commits": []}
