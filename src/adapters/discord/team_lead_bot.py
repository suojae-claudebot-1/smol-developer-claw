"""Team Lead bot adapter — overrides execute_action for team management."""

import sys
from typing import Dict

from src.domain.agent import AgentBrain
from src.domain.personas import TEAM_LEAD_PERSONA
from src.adapters.discord.notification import DiscordBotAdapter


def _log(msg: str):
    print(msg, file=sys.stderr)


class TeamLeadAdapter(DiscordBotAdapter):
    """TeamLead-specific adapter that enables FIRE/HIRE/STATUS actions."""

    def __init__(self, brain: AgentBrain, token: str, bot_registry: Dict[str, AgentBrain] = None, **kwargs):
        super().__init__(brain=brain, token=token, **kwargs)
        self._bot_registry: Dict[str, AgentBrain] = bot_registry or {}

    @property
    def bot_registry(self) -> Dict[str, AgentBrain]:
        return self._bot_registry

    @bot_registry.setter
    def bot_registry(self, value: Dict[str, AgentBrain]):
        self._bot_registry = value

    async def _respond(self, msg):
        """Override to inject team management action handling."""
        if not self._brain.executor or not self._brain._notification:
            return

        from src.config import MODEL_ALIASES
        from src.domain.action_parser import parse_actions, strip_actions, escape_mentions

        await self._brain._notification.send_typing(msg.channel_id)

        context = self._brain.build_context(msg.channel_id, msg.content)
        try:
            response = await self._brain.executor.execute(
                msg.content,
                system_prompt=context,
                model=MODEL_ALIASES[self._brain._current_model],
            )
        except Exception as e:
            _log(f"[{self._brain.bot_name}] LLM error: {e}")
            return

        actions = parse_actions(response)
        clean = strip_actions(response)
        if clean:
            safe = escape_mentions(clean)
            for chunk in self._brain._split_message(safe):
                await self._brain._notification.send(msg.channel_id, chunk)

        for action in actions:
            # TeamLead handles team management actions directly
            if action.action_type in ("FIRE_BOT", "HIRE_BOT", "STATUS_REPORT"):
                from src.domain.team_manager import fire_bot, hire_bot, status_report
                if action.action_type == "FIRE_BOT":
                    result = await fire_bot(action.body.strip(), self._bot_registry, self._brain.bot_name)
                elif action.action_type == "HIRE_BOT":
                    result = await hire_bot(action.body.strip(), self._bot_registry, self._brain.bot_name)
                else:
                    result = status_report(self._bot_registry, self._brain.bot_name)
            else:
                result = await self._brain.execute_action(
                    action.action_type, action.body,
                    channel_id=msg.channel_id, author=msg.author_name,
                )
            if result:
                await self._brain._notification.send(msg.channel_id, result)

        self._brain.save_to_history(msg.channel_id, msg.content, response[:200])
