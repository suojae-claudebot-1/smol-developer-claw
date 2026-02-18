"""AgentBrain — core agent logic, no framework dependencies.

Encapsulates message routing, command handling, and GitHub action execution
without any Discord dependency.
"""

import asyncio
import sys
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import CONFIG, DEFAULT_MODEL, MODEL_ALIASES
from src.domain.action_parser import (
    ACTION_MAP,
    MAX_ACTIONS_PER_MESSAGE,
    escape_mentions,
    parse_actions,
    parse_kv_body,
    strip_actions,
    parse_review_body,
    parse_pr_body,
    parse_issue_body,
    parse_comment_body,
    parse_merge_body,
    parse_read_file_body,
    parse_get_pr_diff_body,
)
from src.domain.models import ActionBlock
from src.ports.inbound import IncomingMessage
from src.ports.outbound import ApprovalPort, GitHubPort, LLMPort, NotificationPort

_TRUNCATE_LIMIT = 8000
_DISCORD_MSG_LIMIT = 2000


def _log(msg: str):
    print(msg, file=sys.stderr)


def _truncate(text: str, limit: int = _TRUNCATE_LIMIT) -> str:
    """Truncate text with indicator if over limit."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (truncated, {len(text)} chars total)"


# Actions that require manual approval before execution
_APPROVAL_REQUIRED_ACTIONS = {"CREATE_PR", "MERGE_PR"}


class AgentBrain:
    """Pure agent logic — no discord import, testable with mock ports.

    Handles:
    - Message routing (should I respond?)
    - Command dispatch (!cancel, !clear, !help)
    - LLM invocation via LLMPort
    - GitHub action block execution
    - Bot chain limiting
    """

    _MAX_CHANNELS = 20

    def __init__(
        self,
        bot_name: str,
        persona: str,
        executor: Optional[LLMPort] = None,
        github: Optional[GitHubPort] = None,
        notification: Optional[NotificationPort] = None,
        approval: Optional[ApprovalPort] = None,
        aliases: Optional[List[str]] = None,
        own_channel_id: int = 0,
        team_channel_ids: Optional[set] = None,
        primary_team_channel_id: int = 0,
    ):
        self.bot_name = bot_name
        self.persona = persona
        self.own_channel_id = own_channel_id
        self.executor = executor

        # Public via properties
        self._aliases: List[str] = aliases or []
        self._primary_team_channel_id = primary_team_channel_id
        self._team_channel_ids = team_channel_ids or set()
        self._github = github
        self._notification = notification
        self._approval = approval
        self._current_model: str = DEFAULT_MODEL
        self._max_bot_chain: int = 3
        self._suppress_bot_replies: bool = False

        self._action_lock = asyncio.Lock()
        self._channel_history: OrderedDict[int, List[Dict[str, str]]] = OrderedDict()
        self._max_history = 10
        self._active: bool = True
        self._rehired: bool = False
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._bot_chain_count: Dict[int, int] = {}

        # Callback for getting a channel reference (set by Discord adapter)
        self._get_channel: Optional[Callable] = None
        # Callback for checking if connection is closed
        self._is_closed: Optional[Callable] = None

    # ── Public properties ────────────────────────────────────

    @property
    def aliases(self) -> List[str]:
        return self._aliases

    @property
    def team_channel_ids(self) -> set:
        return self._team_channel_ids

    @property
    def notification(self) -> Optional[NotificationPort]:
        return self._notification

    @property
    def current_model(self) -> str:
        return self._current_model

    @property
    def max_bot_chain(self) -> int:
        return self._max_bot_chain

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool):
        self._active = value

    @property
    def rehired(self) -> bool:
        return self._rehired

    @rehired.setter
    def rehired(self, value: bool):
        self._rehired = value

    def history_message_count(self) -> int:
        """Total message count across all channels (for status reports)."""
        return sum(len(h) for h in self._channel_history.values())

    def wire(
        self,
        notification: NotificationPort,
        get_channel: Callable,
        is_closed: Callable,
    ):
        """Wire up adapter-provided callbacks. Called by DiscordBotAdapter.on_ready."""
        self._notification = notification
        self._get_channel = get_channel
        self._is_closed = is_closed

    def clear_history(self):
        """Clear all conversation history."""
        self._channel_history.clear()
        _log(f"[{self.bot_name}] conversation history cleared")

    # ── Message routing ──────────────────────────────────────

    def should_respond(self, msg: IncomingMessage) -> bool:
        """Determine if this brain should respond to the message."""
        if not self._active:
            return False

        if msg.is_bot:
            if self._suppress_bot_replies:
                return False
            return msg.is_team_channel and msg.is_mention

        # User messages
        if msg.is_own_channel:
            return True
        if msg.is_team_channel and msg.is_mention:
            return True
        return False

    def is_command(self, content: str) -> Optional[str]:
        """Check if content is a command. Returns command name or None."""
        stripped = content.strip()
        if not stripped:
            return None
        cmd = stripped.split()[0].lower()
        if cmd in ("!cancel", "!clear", "!help"):
            return cmd
        return None

    # ── Bot chain control (domain logic) ─────────────────────

    def get_chain_count(self, channel_id: int) -> int:
        """Get current bot chain count for a channel."""
        return self._bot_chain_count.get(channel_id, 0)

    def increment_chain(self, channel_id: int):
        """Increment bot chain counter."""
        self._bot_chain_count[channel_id] = self._bot_chain_count.get(channel_id, 0) + 1

    def reset_chain(self, channel_id: int):
        """Reset bot chain counter (on human message)."""
        self._suppress_bot_replies = False
        self._bot_chain_count[channel_id] = 0

    def check_and_update_chain(self, msg: IncomingMessage) -> bool:
        """Check bot chain status and update counters.

        Returns True if the message should be processed, False if suppressed.
        """
        if msg.is_bot:
            self.increment_chain(msg.channel_id)
            if self.get_chain_count(msg.channel_id) > self._max_bot_chain:
                _log(f"[{self.bot_name}] chain limit reached in ch={msg.channel_id}")
                self._suppress_bot_replies = True
                return False
        else:
            self.reset_chain(msg.channel_id)
        return True

    def suppress_bot_replies(self):
        """Suppress bot replies (e.g. after !cancel)."""
        self._suppress_bot_replies = True

    def cancel_own_tasks(self) -> int:
        """Cancel all of this brain's active tasks across all channels."""
        cancelled = 0
        for ch_id, task in list(self._active_tasks.items()):
            if task and not task.done():
                task.cancel()
                cancelled += 1
        return cancelled

    # ── Context & history ────────────────────────────────────

    def build_context(self, channel_id: int, user_message: str) -> str:
        """Build LLM context from persona + history."""
        if channel_id in self._channel_history:
            self._channel_history.move_to_end(channel_id)
        else:
            self._channel_history[channel_id] = []
            while len(self._channel_history) > self._MAX_CHANNELS:
                evicted_id, _ = self._channel_history.popitem(last=False)
                _log(f"[{self.bot_name}] evicted channel history: {evicted_id}")
        history = self._channel_history[channel_id]

        parts = [self.persona]

        if self._rehired:
            parts.append(
                "[시스템 알림] 너는 방금 해고(컨텍스트 초기화) 후 재채용되었음. "
                "이전 대화 기록은 전부 삭제된 상태임. "
                "새로 온보딩한다고 생각하고, 팀에 합류 인사 후 업무에 바로 복귀할 것."
            )
            self._rehired = False

        if history:
            lines = [f"{h['role']}: {h['text']}" for h in history[-self._max_history:]]
            parts.append("Previous conversation:\n" + "\n".join(lines))
        parts.append("Continue naturally.")
        return "\n\n".join(parts)

    def save_to_history(self, channel_id: int, user_message: str, response: str):
        """Save exchange to channel history."""
        history = self._channel_history.get(channel_id, [])
        history.append({"role": "user", "text": user_message})
        history.append({"role": "assistant", "text": response[:200]})
        if len(history) > self._max_history * 2:
            history = history[-self._max_history * 2:]
        self._channel_history[channel_id] = history

    # ── LLM call + action parsing (shared logic) ────────────

    async def process_message(self, msg: IncomingMessage) -> Optional[Tuple[str, List[ActionBlock]]]:
        """Run LLM and parse response into text + actions.

        Returns (raw_response, actions) or None on failure.
        Sends typing indicator and clean text via notification.
        """
        if not self.executor or not self._notification:
            return None

        await self._notification.send_typing(msg.channel_id)

        context = self.build_context(msg.channel_id, msg.content)
        try:
            response = await self.executor.execute(
                msg.content,
                system_prompt=context,
                model=MODEL_ALIASES[self._current_model],
            )
        except Exception as e:
            _log(f"[{self.bot_name}] LLM error: {e}")
            return None

        actions = parse_actions(response)
        clean = strip_actions(response)
        if clean:
            safe = escape_mentions(clean)
            for chunk in self._split_message(safe):
                await self._notification.send(msg.channel_id, chunk)

        self.save_to_history(msg.channel_id, msg.content, response[:200])
        return response, actions

    # ── GitHub action execution ──────────────────────────────

    async def _execute_review_pr(self, body: str) -> str:
        """Execute REVIEW_PR action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_review_body(body)
        try:
            pr_number = int(fields.get("pr_number", "0"))
        except (ValueError, TypeError):
            return f"[{self.bot_name}] 잘못된 PR 번호: {fields.get('pr_number')}"
        if not pr_number:
            return f"[{self.bot_name}] PR 번호가 누락됨."
        event = fields.get("event", "COMMENT").strip().upper()
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            return f"[{self.bot_name}] 잘못된 리뷰 이벤트: {event}"
        review_body = fields.get("body", "").strip()
        if not review_body:
            return f"[{self.bot_name}] 리뷰 내용이 비어있음."
        try:
            result = await self._github.review_pr(pr_number, review_body, event)
            if result.success:
                return f"[{self.bot_name}] PR #{pr_number} 리뷰 완료 ({event})"
            return f"[{self.bot_name}] PR #{pr_number} 리뷰 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] PR 리뷰 에러: {e}"

    async def _execute_comment_pr(self, body: str) -> str:
        """Execute COMMENT_PR action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_comment_body(body)
        try:
            pr_number = int(fields.get("pr_number", "0"))
        except (ValueError, TypeError):
            return f"[{self.bot_name}] 잘못된 PR 번호: {fields.get('pr_number')}"
        if not pr_number:
            return f"[{self.bot_name}] PR 번호가 누락됨."
        comment_body = fields.get("body", "").strip()
        if not comment_body:
            return f"[{self.bot_name}] 코멘트 내용이 비어있음."
        try:
            result = await self._github.comment_pr(pr_number, comment_body)
            if result.success:
                return f"[{self.bot_name}] PR #{pr_number} 코멘트 완료"
            return f"[{self.bot_name}] PR #{pr_number} 코멘트 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] PR 코멘트 에러: {e}"

    async def _execute_create_issue(self, body: str) -> str:
        """Execute CREATE_ISSUE action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_issue_body(body)
        title = fields.get("title", "").strip()
        if not title:
            return f"[{self.bot_name}] 이슈 제목이 누락됨."
        issue_body = fields.get("body", "").strip()
        labels_str = fields.get("labels", "").strip()
        labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else None
        try:
            result = await self._github.create_issue(title, issue_body, labels)
            if result.success:
                return f"[{self.bot_name}] 이슈 생성 완료: {result.url}"
            return f"[{self.bot_name}] 이슈 생성 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] 이슈 생성 에러: {e}"

    async def _execute_create_pr(self, body: str) -> str:
        """Execute CREATE_PR action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_pr_body(body)
        title = fields.get("title", "").strip()
        if not title:
            return f"[{self.bot_name}] PR 제목이 누락됨."
        pr_body = fields.get("body", "").strip()
        head = fields.get("head", "").strip()
        if not head:
            return f"[{self.bot_name}] 소스 브랜치(head)가 누락됨."
        base = fields.get("base", "main").strip()
        try:
            result = await self._github.create_pr(title, pr_body, head, base)
            if result.success:
                return f"[{self.bot_name}] PR 생성 완료: {result.url} (#{result.pr_number})"
            return f"[{self.bot_name}] PR 생성 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] PR 생성 에러: {e}"

    async def _execute_merge_pr(self, body: str) -> str:
        """Execute MERGE_PR action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_merge_body(body)
        try:
            pr_number = int(fields.get("pr_number", "0"))
        except (ValueError, TypeError):
            return f"[{self.bot_name}] 잘못된 PR 번호: {fields.get('pr_number')}"
        if not pr_number:
            return f"[{self.bot_name}] PR 번호가 누락됨."
        method = fields.get("method", "squash").strip()
        try:
            result = await self._github.merge_pr(pr_number, method)
            if result.success:
                return f"[{self.bot_name}] PR #{pr_number} 머지 완료 ({method})"
            return f"[{self.bot_name}] PR #{pr_number} 머지 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] PR 머지 에러: {e}"

    async def _execute_get_pr_diff(self, body: str) -> str:
        """Execute GET_PR_DIFF action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_get_pr_diff_body(body)
        try:
            pr_number = int(fields.get("pr_number", "0"))
        except (ValueError, TypeError):
            return f"[{self.bot_name}] 잘못된 PR 번호: {fields.get('pr_number')}"
        if not pr_number:
            return f"[{self.bot_name}] PR 번호가 누락됨."
        try:
            result = await self._github.get_pr_diff(pr_number)
            if result.success:
                diff = _truncate(result.content or "")
                return f"[{self.bot_name}] PR #{pr_number} diff:\n```\n{diff}\n```"
            return f"[{self.bot_name}] PR #{pr_number} diff 조회 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] PR diff 조회 에러: {e}"

    async def _execute_read_file(self, body: str) -> str:
        """Execute READ_FILE action."""
        if not self._github:
            return f"[{self.bot_name}] GitHub 클라이언트가 연결되지 않았음."
        fields = parse_read_file_body(body)
        path = fields.get("path", "").strip()
        if not path:
            return f"[{self.bot_name}] 파일 경로가 누락됨."
        ref = fields.get("ref", "main").strip()
        try:
            result = await self._github.read_file(path, ref)
            if result.success:
                content = _truncate(result.content or "")
                return f"[{self.bot_name}] `{path}` ({ref}):\n```\n{content}\n```"
            return f"[{self.bot_name}] 파일 읽기 실패: {result.error}"
        except Exception as e:
            return f"[{self.bot_name}] 파일 읽기 에러: {e}"

    # ── Action dispatch ──────────────────────────────────────

    async def execute_action(self, action_type: str, body: str,
                             channel_id: int = 0, author: str = "") -> str:
        """Execute an action block. Can be overridden by subclasses."""
        mapping = ACTION_MAP.get(action_type)
        if not mapping:
            return f"[{self.bot_name}] 알 수 없는 액션: {action_type}"

        platform, action_kind = mapping

        # Team management actions — rejected by default (adapter overrides for TeamLead)
        if action_type in ("FIRE_BOT", "HIRE_BOT", "STATUS_REPORT"):
            return f"[{self.bot_name}] 팀 관리 액션은 TeamLead만 실행 가능함."

        if not body:
            return f"[{self.bot_name}] 액션 본문이 비어있음. ({action_type})"

        # Approval check for high-impact actions
        if action_type in _APPROVAL_REQUIRED_ACTIONS and CONFIG["require_manual_approval"]:
            if self._approval:
                result = await self._approval.enqueue(platform, action_kind, body)
                return f"[{self.bot_name}] 승인 대기 중 (ID: {result['approval_id']})"

        # GitHub action dispatch
        dispatch = {
            "REVIEW_PR": self._execute_review_pr,
            "COMMENT_PR": self._execute_comment_pr,
            "CREATE_ISSUE": self._execute_create_issue,
            "CREATE_PR": self._execute_create_pr,
            "MERGE_PR": self._execute_merge_pr,
            "GET_PR_DIFF": self._execute_get_pr_diff,
            "READ_FILE": self._execute_read_file,
        }
        handler = dispatch.get(action_type)
        if handler:
            return await handler(body)

        return f"[{self.bot_name}] 처리되지 않은 액션: {action_type}"

    @staticmethod
    def _split_message(text: str, limit: int = _DISCORD_MSG_LIMIT) -> List[str]:
        """Split a message into chunks that fit Discord's character limit."""
        if len(text) <= limit:
            return [text]
        chunks = []
        while text:
            chunks.append(text[:limit])
            text = text[limit:]
        return chunks
