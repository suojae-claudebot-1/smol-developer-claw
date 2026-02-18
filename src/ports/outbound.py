"""Outbound ports — interfaces for external system adapters."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class GitHubResult:
    """Unified result type for GitHub operations."""

    success: bool
    url: Optional[str] = None
    pr_number: Optional[int] = None
    content: Optional[str] = None
    error: Optional[str] = None


@runtime_checkable
class LLMPort(Protocol):
    """Interface for LLM execution backends."""

    async def execute(
        self,
        message: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str: ...


@runtime_checkable
class GitHubPort(Protocol):
    """Interface for GitHub API operations."""

    async def review_pr(self, pr_number: int, body: str, event: str = "COMMENT") -> GitHubResult: ...

    async def comment_pr(self, pr_number: int, body: str) -> GitHubResult: ...

    async def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> GitHubResult: ...

    async def create_pr(
        self, title: str, body: str, head: str, base: str = "main",
    ) -> GitHubResult: ...

    async def merge_pr(self, pr_number: int, merge_method: str = "squash") -> GitHubResult: ...

    async def get_pr_diff(self, pr_number: int) -> GitHubResult: ...

    async def read_file(self, path: str, ref: str = "main") -> GitHubResult: ...


@runtime_checkable
class StoragePort(Protocol):
    """Interface for persistent storage."""

    def load(self, key: str) -> list: ...
    def save(self, key: str, data: list) -> None: ...


@runtime_checkable
class NotificationPort(Protocol):
    """Interface for sending messages to channels."""

    async def send(self, channel_id: int, text: str) -> None: ...
    async def send_typing(self, channel_id: int) -> None: ...


@runtime_checkable
class ApprovalPort(Protocol):
    """Interface for action approval queue."""

    async def enqueue(
        self,
        platform: str,
        action_kind: str,
        text: str,
        meta: Optional[Dict[str, str]] = None,
    ) -> dict: ...
