"""Launcher for the multi-bot Discord system."""

import asyncio
import sys
from typing import Dict, Optional

from src.config import DISCORD_CHANNELS, DISCORD_TOKENS
from src.domain.agent import AgentBrain
from src.domain.personas import (
    TEAM_LEAD_PERSONA,
    FLUTTER_DEV_PERSONA,
    BACKEND_REVIEW_PERSONA,
    FULLSTACK_REVIEW_PERSONA,
    FRONTEND_REVIEW_PERSONA,
    MOBILE_REVIEW_PERSONA,
)
from src.adapters.discord.notification import DiscordBotAdapter
from src.adapters.discord.team_lead_bot import TeamLeadAdapter


def _log(msg: str):
    print(msg, file=sys.stderr)


def _create_executor():
    """Create an executor for all bots.

    Tries real AI executor first (ClaudeExecutor/CodexExecutor),
    falls back to local passthrough if unavailable.
    """
    try:
        from src.adapters.llm.executor import create_executor
        return create_executor()
    except Exception as e:
        _log(f"AI executor unavailable ({e}), using passthrough")

    class _Passthrough:
        usage_tracker = None

        async def execute(self, message: str, system_prompt: Optional[str] = None,
                          session_id: Optional[str] = None, model: Optional[str] = None) -> str:
            return "메시지 확인했음. (passthrough 모드 — AI executor 미연결)"

    return _Passthrough()


def _create_github_client():
    """Create GitHub client with graceful degradation."""
    try:
        from src.adapters.github.client import GitHubClient
        client = GitHubClient()
        if client.is_configured:
            _log("GitHubClient loaded")
            return client
        _log("GitHubClient not configured — skipping")
    except Exception as e:
        _log(f"GitHubClient unavailable: {e}")
    return None


_BOT_REGISTRY: Dict[str, AgentBrain] = {}


# Bot definitions: (key, persona, token_key, aliases)
_BOT_DEFS = [
    ("flutter", FLUTTER_DEV_PERSONA, "flutter", ["FlutterDevBot", "FlutterDev"]),
    ("backend_review", BACKEND_REVIEW_PERSONA, "backend_review", ["BackendReviewBot", "BackendReview"]),
    ("fullstack_review", FULLSTACK_REVIEW_PERSONA, "fullstack_review", ["FullstackReviewBot", "FullstackReview"]),
    ("frontend_review", FRONTEND_REVIEW_PERSONA, "frontend_review", ["FrontendReviewBot", "FrontendReview"]),
    ("mobile_review", MOBILE_REVIEW_PERSONA, "mobile_review", ["MobileReviewBot", "MobileReview"]),
]


def _build_bots():
    """Instantiate all bots with their channel configs and GitHub client."""
    _BOT_REGISTRY.clear()

    team_ch = DISCORD_CHANNELS["team"]
    test_ch = DISCORD_CHANNELS.get("test", 0)
    team_ids = {team_ch}
    if test_ch:
        team_ids.add(test_ch)

    github = _create_github_client()
    bots = []

    # TeamLead — special adapter with team management
    lead_token = DISCORD_TOKENS["lead"]
    if lead_token:
        lead_brain = AgentBrain(
            bot_name="TeamLead",
            persona=TEAM_LEAD_PERSONA,
            executor=_create_executor(),
            github=github,
            own_channel_id=DISCORD_CHANNELS["lead"],
            team_channel_ids=team_ids,
            primary_team_channel_id=team_ch,
            aliases=["TeamLead", "Captain"],
        )
        lead_adapter = TeamLeadAdapter(brain=lead_brain, token=lead_token)
        _BOT_REGISTRY["lead"] = lead_brain
        bots.append((lead_adapter, lead_token))
    else:
        _log("Skipping TeamLead — DISCORD_LEAD_TOKEN not set")

    # Sub-bots
    for key, persona, token_key, aliases in _BOT_DEFS:
        token = DISCORD_TOKENS[token_key]
        if not token:
            _log(f"Skipping {aliases[0]} — DISCORD_{token_key.upper()}_TOKEN not set")
            continue
        brain = AgentBrain(
            bot_name=aliases[0],
            persona=persona,
            executor=_create_executor(),
            github=github,
            own_channel_id=DISCORD_CHANNELS[key],
            team_channel_ids=team_ids,
            primary_team_channel_id=team_ch,
            aliases=aliases,
        )
        adapter = DiscordBotAdapter(brain=brain, token=token)
        _BOT_REGISTRY[key] = brain
        bots.append((adapter, token))

    # Inject bot_registry into TeamLead adapter
    for adapter, _ in bots:
        if isinstance(adapter, TeamLeadAdapter):
            adapter.bot_registry = _BOT_REGISTRY

    return bots


async def launch_all_bots():
    """Launch all configured bots concurrently."""
    bots = _build_bots()

    if not bots:
        _log("No bots configured. Set DISCORD_*_TOKEN environment variables.")
        return

    _log(f"Launching {len(bots)} bot(s)...")

    async def _run(bot, token):
        try:
            await bot.start(token)
        except Exception as e:
            _log(f"[{bot._brain.bot_name}] crashed: {e}")

    await asyncio.gather(*[_run(bot, token) for bot, token in bots])


if __name__ == "__main__":
    asyncio.run(launch_all_bots())
