"""Domain data models — pure Python dataclasses."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionBlock:
    """Parsed action from LLM response."""

    action_type: str  # e.g. "REVIEW_PR", "CREATE_PR"
    body: str


@dataclass
class ReviewVerdict:
    """Structured PR review result."""

    pr_number: int
    event: str  # "APPROVE" | "REQUEST_CHANGES" | "COMMENT"
    body: str
