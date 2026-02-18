"""GitHub API adapter — implements GitHubPort."""

import asyncio
import sys
from typing import List, Optional

from src.config import CONFIG
from src.ports.outbound import GitHubResult


def _log(msg: str):
    print(msg, file=sys.stderr)


class GitHubClient:
    """GitHub API client implementing GitHubPort protocol.

    Uses PyGithub for synchronous API calls, wrapped in asyncio.to_thread
    for non-blocking execution.
    """

    def __init__(self, token: str = "", repo: str = ""):
        self._token = token or CONFIG["github_token"]
        self._repo_name = repo or CONFIG["github_repo"]
        self._gh = None
        self._repo = None

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._repo_name)

    def _ensure_client(self):
        if self._gh is None:
            from github import Github
            self._gh = Github(self._token)
            self._repo = self._gh.get_repo(self._repo_name)

    async def review_pr(self, pr_number: int, body: str, event: str = "COMMENT") -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                pr = self._repo.get_pull(pr_number)
                pr.create_review(body=body, event=event)
                return pr.html_url
            url = await asyncio.to_thread(_do)
            return GitHubResult(success=True, url=url, pr_number=pr_number)
        except Exception as e:
            _log(f"[GitHubClient] review_pr error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def comment_pr(self, pr_number: int, body: str) -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                pr = self._repo.get_pull(pr_number)
                comment = pr.create_issue_comment(body=body)
                return pr.html_url
            url = await asyncio.to_thread(_do)
            return GitHubResult(success=True, url=url, pr_number=pr_number)
        except Exception as e:
            _log(f"[GitHubClient] comment_pr error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                kwargs = {"title": title, "body": body}
                if labels:
                    kwargs["labels"] = labels
                issue = self._repo.create_issue(**kwargs)
                return issue.html_url
            url = await asyncio.to_thread(_do)
            return GitHubResult(success=True, url=url)
        except Exception as e:
            _log(f"[GitHubClient] create_issue error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def create_pr(self, title: str, body: str, head: str, base: str = "main") -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                pr = self._repo.create_pull(title=title, body=body, head=head, base=base)
                return pr.html_url, pr.number
            url, number = await asyncio.to_thread(_do)
            return GitHubResult(success=True, url=url, pr_number=number)
        except Exception as e:
            _log(f"[GitHubClient] create_pr error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def merge_pr(self, pr_number: int, merge_method: str = "squash") -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                pr = self._repo.get_pull(pr_number)
                result = pr.merge(merge_method=merge_method)
                return pr.html_url, result.merged
            url, merged = await asyncio.to_thread(_do)
            if merged:
                return GitHubResult(success=True, url=url, pr_number=pr_number)
            return GitHubResult(success=False, error="merge failed", pr_number=pr_number)
        except Exception as e:
            _log(f"[GitHubClient] merge_pr error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def get_pr_diff(self, pr_number: int) -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                pr = self._repo.get_pull(pr_number)
                files = pr.get_files()
                diff_parts = []
                for f in files:
                    diff_parts.append(f"--- {f.filename}\n{f.patch or '(binary)'}")
                return "\n\n".join(diff_parts)
            content = await asyncio.to_thread(_do)
            return GitHubResult(success=True, content=content, pr_number=pr_number)
        except Exception as e:
            _log(f"[GitHubClient] get_pr_diff error: {e}")
            return GitHubResult(success=False, error=str(e))

    async def read_file(self, path: str, ref: str = "main") -> GitHubResult:
        try:
            def _do():
                self._ensure_client()
                contents = self._repo.get_contents(path, ref=ref)
                return contents.decoded_content.decode("utf-8")
            content = await asyncio.to_thread(_do)
            return GitHubResult(success=True, content=content)
        except Exception as e:
            _log(f"[GitHubClient] read_file error: {e}")
            return GitHubResult(success=False, error=str(e))
