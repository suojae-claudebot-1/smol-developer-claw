"""Team Lead bot adapter — overrides action execution for team management."""

import sys
from typing import Dict

from src.domain.agent import AgentBrain
from src.adapters.discord.notification import DiscordBotAdapter


def _log(msg: str):
    print(msg, file=sys.stderr)


class TeamLeadAdapter(DiscordBotAdapter):
    """TeamLead-specific adapter that enables FIRE/HIRE/STATUS actions.

    Only overrides _execute_single_action — all common LLM/response logic
    is inherited from DiscordBotAdapter._respond via process_message.
    """

    def __init__(self, brain: AgentBrain, token: str, bot_registry: Dict[str, AgentBrain] = None, **kwargs):
        super().__init__(brain=brain, token=token, **kwargs)
        self._bot_registry: Dict[str, AgentBrain] = bot_registry or {}

    @property
    def bot_registry(self) -> Dict[str, AgentBrain]:
        return self._bot_registry

    @bot_registry.setter
    def bot_registry(self, value: Dict[str, AgentBrain]):
        self._bot_registry = value

    async def _execute_single_action(self, action, channel_id: int, author: str) -> str:
        """Override to handle team management actions directly."""
        if action.action_type in ("FIRE_BOT", "HIRE_BOT", "STATUS_REPORT"):
            from src.domain.team_manager import fire_bot, hire_bot, status_report
            if action.action_type == "FIRE_BOT":
                return await fire_bot(action.body.strip(), self._bot_registry, self._brain.bot_name)
            elif action.action_type == "HIRE_BOT":
                return await hire_bot(action.body.strip(), self._bot_registry, self._brain.bot_name)
            else:
                return status_report(self._bot_registry, self._brain.bot_name)

        return await super()._execute_single_action(action, channel_id, author)
