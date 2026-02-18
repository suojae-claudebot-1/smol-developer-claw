"""Action block parsing — GitHub-oriented action system.

Pure Python, no framework dependencies.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List, Tuple

from src.domain.models import ActionBlock

if TYPE_CHECKING:
    from src.domain.alarm import AlarmEntry

# Action block regex: [ACTION:TYPE] ... [/ACTION]
ACTION_RE = re.compile(
    r"\[ACTION:(\w+)\]\s*(.*?)\s*\[/ACTION\]",
    re.DOTALL,
)

# Map ACTION codes -> (platform, action_kind)
ACTION_MAP: Dict[str, Tuple[str, str]] = {
    "REVIEW_PR": ("github", "review_pr"),
    "COMMENT_PR": ("github", "comment_pr"),
    "CREATE_ISSUE": ("github", "create_issue"),
    "CREATE_PR": ("github", "create_pr"),
    "MERGE_PR": ("github", "merge_pr"),
    "READ_FILE": ("github", "read_file"),
    "GET_PR_DIFF": ("github", "get_pr_diff"),
    "SET_ALARM": ("alarm", "set"),
    "CANCEL_ALARM": ("alarm", "cancel"),
    "FIRE_BOT": ("team", "fire"),
    "HIRE_BOT": ("team", "hire"),
    "STATUS_REPORT": ("team", "status"),
}

# Max actions per single LLM response (spam prevention)
MAX_ACTIONS_PER_MESSAGE = 3


def parse_actions(text: str) -> List[ActionBlock]:
    """Extract action blocks from LLM response text."""
    return [
        ActionBlock(action_type=action_type, body=body.strip())
        for action_type, body in ACTION_RE.findall(text)
    ]


def strip_actions(text: str) -> str:
    """Remove all action blocks from text."""
    return ACTION_RE.sub("", text).strip()


def escape_mentions(text: str) -> str:
    """Escape @mentions to prevent triggering other bots."""
    return re.sub(r"@(\w+)", r"`@\1`", text)


def parse_kv_body(body: str) -> Dict[str, str]:
    """Parse key: value lines from action body.

    Lines without a colon are appended to the previous key's value,
    supporting multiline prompt fields.
    """
    fields: Dict[str, str] = {}
    last_key = None
    for line in body.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            fields[key] = value.strip()
            last_key = key
        elif last_key is not None:
            fields[last_key] += "\n" + line
    return fields


def parse_review_body(body: str) -> Dict[str, str]:
    """Parse REVIEW_PR body — pr_number, event, body."""
    return parse_kv_body(body)


def parse_pr_body(body: str) -> Dict[str, str]:
    """Parse CREATE_PR body — title, body, head, base."""
    return parse_kv_body(body)


def parse_issue_body(body: str) -> Dict[str, str]:
    """Parse CREATE_ISSUE body — title, body, labels."""
    return parse_kv_body(body)


def parse_comment_body(body: str) -> Dict[str, str]:
    """Parse COMMENT_PR body — pr_number, body."""
    return parse_kv_body(body)


def parse_merge_body(body: str) -> Dict[str, str]:
    """Parse MERGE_PR body — pr_number, method."""
    return parse_kv_body(body)


def parse_read_file_body(body: str) -> Dict[str, str]:
    """Parse READ_FILE body — path, ref."""
    return parse_kv_body(body)


def parse_get_pr_diff_body(body: str) -> Dict[str, str]:
    """Parse GET_PR_DIFF body — pr_number."""
    return parse_kv_body(body)


def format_schedule(alarm: "AlarmEntry") -> str:
    """Format alarm schedule for display."""
    if alarm.schedule_type == "daily":
        return f"매일 {alarm.hour:02d}:{alarm.minute:02d}"
    elif alarm.schedule_type == "weekday":
        return f"평일 {alarm.hour:02d}:{alarm.minute:02d}"
    elif alarm.schedule_type == "interval":
        if alarm.interval_minutes >= 60 and alarm.interval_minutes % 60 == 0:
            return f"{alarm.interval_minutes // 60}시간마다"
        return f"{alarm.interval_minutes}분마다"
    elif alarm.schedule_type == "once":
        if alarm.interval_minutes >= 60 and alarm.interval_minutes % 60 == 0:
            return f"{alarm.interval_minutes // 60}시간 후 1회"
        return f"{alarm.interval_minutes}분 후 1회"
    return alarm.schedule_type
