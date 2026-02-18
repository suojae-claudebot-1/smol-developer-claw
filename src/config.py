"""Configuration and shared state."""

__version__ = "0.1.0"

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Dict

from dotenv import load_dotenv

load_dotenv()

_stderr_print = lambda *a, **kw: print(*a, **kw, file=sys.stderr)

SUPPORTED_AI_PROVIDERS = ("claude", "codex")
AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").strip().lower()
if AI_PROVIDER not in SUPPORTED_AI_PROVIDERS:
    _stderr_print(f"Unsupported AI_PROVIDER={AI_PROVIDER!r}, falling back to 'claude'")
    AI_PROVIDER = "claude"

MODEL_ALIASES_BY_PROVIDER = {
    "claude": {
        "opus": os.getenv("CLAUDE_MODEL_OPUS", "claude-opus-4-6"),
        "sonnet": os.getenv("CLAUDE_MODEL_SONNET", "claude-sonnet-4-5-20250929"),
        "haiku": os.getenv("CLAUDE_MODEL_HAIKU", "claude-haiku-4-5-20251001"),
    },
    "codex": {
        "opus": os.getenv("CODEX_MODEL_OPUS", "gpt-5.3-codex"),
        "sonnet": os.getenv("CODEX_MODEL_SONNET", "gpt-5.3-codex"),
        "haiku": os.getenv("CODEX_MODEL_HAIKU", "gpt-5.3-codex-mini"),
    },
}

MODEL_ALIASES = MODEL_ALIASES_BY_PROVIDER[AI_PROVIDER]

DEFAULT_MODEL = os.getenv("AI_DEFAULT_MODEL", "sonnet").strip().lower()
if DEFAULT_MODEL not in MODEL_ALIASES:
    fallback = "sonnet" if "sonnet" in MODEL_ALIASES else next(iter(MODEL_ALIASES))
    _stderr_print(
        f"Unsupported AI_DEFAULT_MODEL={DEFAULT_MODEL!r} for provider={AI_PROVIDER!r}, "
        f"falling back to {fallback!r}"
    )
    DEFAULT_MODEL = fallback

API_KEY = os.getenv("API_KEY", "")

CONFIG = {
    "port": 3000,
    "session_id": str(uuid.uuid4()),
    "ai_provider": AI_PROVIDER,
    # GitHub
    "github_token": os.getenv("GITHUB_TOKEN", ""),
    "github_repo": os.getenv("GITHUB_REPO", ""),  # owner/repo
    # Usage limits
    "usage_limits": {
        "max_calls_per_minute": 60,
        "max_calls_per_hour": 500,
        "max_calls_per_day": 10000,
        "min_call_interval_seconds": 1,
        "warning_threshold_pct": 80,
        "paused": False,
    },
    # Require explicit human approval before CREATE_PR / MERGE_PR
    "require_manual_approval": os.getenv("REQUIRE_MANUAL_APPROVAL", "true").strip().lower()
    in ("1", "true", "yes", "on"),
}

# Discord multi-bot channel IDs
DISCORD_CHANNELS = {
    "team": int(os.getenv("DISCORD_TEAM_CHANNEL_ID", "0")),
    "test": int(os.getenv("DISCORD_TEST_CHANNEL_ID", "0")),
    "lead": int(os.getenv("DISCORD_LEAD_CHANNEL_ID", "0")),
    "flutter": int(os.getenv("DISCORD_FLUTTER_CHANNEL_ID", "0")),
    "backend_review": int(os.getenv("DISCORD_BACKEND_REVIEW_CHANNEL_ID", "0")),
    "fullstack_review": int(os.getenv("DISCORD_FULLSTACK_REVIEW_CHANNEL_ID", "0")),
    "frontend_review": int(os.getenv("DISCORD_FRONTEND_REVIEW_CHANNEL_ID", "0")),
    "mobile_review": int(os.getenv("DISCORD_MOBILE_REVIEW_CHANNEL_ID", "0")),
}

# Discord multi-bot tokens
DISCORD_TOKENS = {
    "lead": os.getenv("DISCORD_LEAD_TOKEN", ""),
    "flutter": os.getenv("DISCORD_FLUTTER_TOKEN", ""),
    "backend_review": os.getenv("DISCORD_BACKEND_REVIEW_TOKEN", ""),
    "fullstack_review": os.getenv("DISCORD_FULLSTACK_REVIEW_TOKEN", ""),
    "frontend_review": os.getenv("DISCORD_FRONTEND_REVIEW_TOKEN", ""),
    "mobile_review": os.getenv("DISCORD_MOBILE_REVIEW_TOKEN", ""),
}


# ── Typed config (new) ──────────────────────────────────────


@dataclass
class UsageLimitsConfig:
    max_calls_per_minute: int = 60
    max_calls_per_hour: int = 500
    max_calls_per_day: int = 10000
    min_call_interval_seconds: int = 1
    warning_threshold_pct: int = 80
    paused: bool = False


@dataclass
class GitHubConfig:
    token: str = ""
    repo: str = ""  # owner/repo


@dataclass
class DiscordConfig:
    channels: Dict[str, int] = field(default_factory=dict)
    tokens: Dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    """Typed configuration — replaces CONFIG dict for new code."""

    port: int = 3000
    session_id: str = ""
    ai_provider: str = "claude"
    default_model: str = "sonnet"
    require_manual_approval: bool = True
    github: GitHubConfig = field(default_factory=GitHubConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    usage_limits: UsageLimitsConfig = field(default_factory=UsageLimitsConfig)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Create AppConfig from environment variables."""
        return cls(
            port=int(os.getenv("PORT", "3000")),
            session_id=CONFIG["session_id"],
            ai_provider=AI_PROVIDER,
            default_model=DEFAULT_MODEL,
            require_manual_approval=CONFIG["require_manual_approval"],
            github=GitHubConfig(
                token=CONFIG["github_token"],
                repo=CONFIG["github_repo"],
            ),
            discord=DiscordConfig(
                channels=dict(DISCORD_CHANNELS),
                tokens=dict(DISCORD_TOKENS),
            ),
            usage_limits=UsageLimitsConfig(**CONFIG["usage_limits"]),
        )
