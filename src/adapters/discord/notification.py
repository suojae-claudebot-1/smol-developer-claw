"""Discord adapter — bridges discord.Client to AgentBrain.

Provides DiscordBotAdapter, a thin discord.Client subclass
that converts Discord messages to IncomingMessage and delegates to AgentBrain.
"""

import sys

import discord

from src.domain.agent import AgentBrain
from src.ports.inbound import IncomingMessage


def _log(msg: str):
    print(msg, file=sys.stderr)


class DiscordNotificationAdapter:
    """NotificationPort implementation using discord.Client."""

    def __init__(self, client: discord.Client):
        self._client = client

    async def send(self, channel_id: int, text: str) -> None:
        channel = self._client.get_channel(channel_id)
        if channel:
            while text:
                await channel.send(text[:2000])
                text = text[2000:]

    async def send_typing(self, channel_id: int) -> None:
        channel = self._client.get_channel(channel_id)
        if channel:
            await channel.typing()


class DiscordBotAdapter(discord.Client):
    """Thin Discord adapter that delegates to AgentBrain.

    Converts discord.Message -> IncomingMessage for platform-agnostic processing.
    """

    def __init__(self, brain: AgentBrain, token: str, **discord_kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **discord_kwargs)
        self._brain = brain
        self._token = token

    def _to_incoming(self, message: discord.Message) -> IncomingMessage:
        """Convert a Discord message to platform-agnostic IncomingMessage."""
        is_mention = (
            (self.user and self.user.mentioned_in(message))
            or self._is_role_mentioned(message)
            or self._is_text_mentioned(message.content)
        )
        return IncomingMessage(
            content=message.content,
            channel_id=message.channel.id,
            author_name=str(message.author),
            author_id=message.author.id,
            is_bot=message.author.bot,
            is_mention=is_mention,
            is_team_channel=message.channel.id in self._brain.team_channel_ids,
            is_own_channel=message.channel.id == self._brain.own_channel_id,
        )

    def _is_role_mentioned(self, message: discord.Message) -> bool:
        if not message.role_mentions or not self.user:
            return False
        bot_names = {self._brain.bot_name.lower()} | {a.lower() for a in self._brain.aliases}
        return any(role.name.lower() in bot_names for role in message.role_mentions)

    def _is_text_mentioned(self, content: str) -> bool:
        if not self.user:
            return False
        names = {self._brain.bot_name, self.user.name}
        if self.user.display_name:
            names.add(self.user.display_name)
        names.update(self._brain.aliases)
        content_lower = content.lower()
        return any(f"@{name.lower()}" in content_lower for name in names)

    async def on_ready(self):
        _log(f"[{self._brain.bot_name}] logged in as {self.user}")
        notification = DiscordNotificationAdapter(self)
        self._brain.wire(notification, self.get_channel, self.is_closed)

    async def on_message(self, message: discord.Message):
        """Convert Discord message and delegate to AgentBrain."""
        if not self.user or message.author == self.user:
            return

        incoming = self._to_incoming(message)

        if not self._brain.should_respond(incoming):
            if not incoming.is_bot:
                self._brain.reset_chain(incoming.channel_id)
            return

        # Chain control — delegated to domain
        if not self._brain.check_and_update_chain(incoming):
            return

        # Check for commands
        cmd = self._brain.is_command(incoming.content)
        if cmd:
            await self._handle_command(cmd, incoming)
            return

        # Normal message — run LLM and respond
        await self._respond(incoming)

    async def _handle_command(self, cmd: str, msg: IncomingMessage):
        """Dispatch command to AgentBrain."""
        notification = self._brain.notification
        if not notification:
            return

        if cmd == "!cancel":
            count = self._brain.cancel_own_tasks()
            self._brain.suppress_bot_replies()
            if msg.is_own_channel:
                await notification.send(msg.channel_id, f"[{self._brain.bot_name}] {count}개 작업 취소됨.")

        elif cmd == "!clear":
            self._brain.clear_history()
            if msg.is_own_channel:
                await notification.send(msg.channel_id, f"[{self._brain.bot_name}] 대화 기록 초기화됨.")

        elif cmd == "!help":
            help_text = (
                f"**[{self._brain.bot_name}] 명령어 목록**\n"
                "`!cancel` — 진행 중인 응답 취소\n"
                "`!clear` — 대화 기록 초기화\n"
                "`!help` — 이 도움말"
            )
            await notification.send(msg.channel_id, help_text)

    async def _respond(self, msg: IncomingMessage):
        """Run LLM via AgentBrain.process_message and execute actions."""
        result = await self._brain.process_message(msg)
        if not result:
            return

        response, actions = result
        notification = self._brain.notification

        for action in actions:
            action_result = await self._execute_single_action(action, msg.channel_id, msg.author_name)
            if action_result and notification:
                await notification.send(msg.channel_id, action_result)

    async def _execute_single_action(self, action, channel_id: int, author: str) -> str:
        """Execute a single action. Override in subclasses for custom handling."""
        return await self._brain.execute_action(
            action.action_type, action.body,
            channel_id=channel_id, author=author,
        )
