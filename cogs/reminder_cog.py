"""
Daily reminder engine for Sigmonions.

Fires once per guild per day at 13:00 (1 PM) in that guild's configured
timezone.  Admins configure it with /remind channel / /remind timezone.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from utils.categoryhistory import get_fact_for
from utils.database import (
    get_active_player_ids,
    get_guild_settings,
    get_leaderboard,
    mark_reminder_sent,
    set_guild_timezone,
    set_reminder_channel,
)

log = logging.getLogger("sigmonions.reminder")

_REMINDER_HOUR = 13          # 1 PM local server time
_MENTION_CHUNK  = 50         # user mentions per message (~1 250 chars each)

# ── Flavour openers (rotated by day-of-year) ──────────────────────────────────
_OPENERS = [
    "☀️ **Rise and grind, word wizard!** The board is set. The groups are hiding. Will you find them?",
    "🧩 **The puzzle won't solve itself.** Your vocabulary is calling — and it sounds like victory.",
    "🔥 **Daily challenge unlocked!** Outthink, outguess, out-category your rivals. It's on.",
    "🎯 **One day, one board, one chance to climb the leaderboard.** What are you waiting for?",
    "⚡ **Words are weapons — sharpen yours today.** The Sigmonions board drops in 3… 2… 1…",
    "🏆 **Champions don't skip Mondays. Or Tuesdays. Or any day.** Get in here.",
    "🌟 **Your streak isn't going to maintain itself.** Today's categories are begging to be found.",
    "🎮 **New day, new groups, new chance to humiliate your friends.** Respectfully. Let's go.",
    "🧠 **Big brain energy required.** Can you find all four groups before anyone else?",
    "💥 **The daily board is live!** History says you're clever. Today's a good day to prove it.",
    "🎲 **Every word has a home — find the four homes today.** Your rivals are already warming up.",
    "🌈 **Four colours. Four groups. One winner.** Could be you. Should be you. Start the game.",
]

def _opener_for(day_of_year: int) -> str:
    return _OPENERS[day_of_year % len(_OPENERS)]


# ── Pagination helper ──────────────────────────────────────────────────────────

def _chunk_mentions(user_ids: list[int], chunk_size: int = _MENTION_CHUNK) -> list[str]:
    """Split user IDs into mention strings that fit inside a 2000-char message."""
    mentions = [f"<@{uid}>" for uid in user_ids]
    chunks = []
    for i in range(0, len(mentions), chunk_size):
        chunks.append(" ".join(mentions[i : i + chunk_size]))
    return chunks


# ── Embed builder ──────────────────────────────────────────────────────────────

def _build_reminder_embed(
    now_local: datetime,
    fact: str,
    leaderboard: list[dict],
) -> discord.Embed:
    opener = _opener_for(now_local.timetuple().tm_yday)
    tz_name = now_local.tzname() or "UTC"

    embed = discord.Embed(
        title="🕐  Daily Sigmonions Challenge!",
        description=opener,
        color=discord.Color.from_rgb(255, 165, 0),   # warm orange — urgent but friendly
    )

    # History fact
    embed.add_field(
        name="📅  On This Day",
        value=f"> {fact}",
        inline=False,
    )

    # Leaderboard teaser — top 3 as motivation
    if leaderboard:
        medals = ["🥇", "🥈", "🥉"]
        lb_lines = [
            f"{medals[i]} <@{r['username']}> — **{r['total_points']:+} pts**"
            if False   # we only have username strings, not IDs from leaderboard
            else f"{medals[i]} **{r['username']}** — {r['total_points']:+} pts"
            for i, r in enumerate(leaderboard[:3])
        ]
        embed.add_field(
            name="🏆  Current Leaders",
            value="\n".join(lb_lines) if lb_lines else "*No scores yet — be the first!*",
            inline=True,
        )

    # CTA
    embed.add_field(
        name="🎮  Ready to play?",
        value="Type `/sigmonion play` in any channel to start today's game!\n"
              "Find four hidden groups of four words — faster guesses earn bonus points.",
        inline=False,
    )

    embed.set_footer(
        text=f"Daily reminder · {now_local.strftime('%A, %d %B %Y')} · {tz_name} time"
    )
    return embed


# ── Cog ────────────────────────────────────────────────────────────────────────

class ReminderCog(commands.Cog):

    remind = discord.SlashCommandGroup(
        "remind",
        "Configure Sigmonions daily reminders for this server",
    )

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._daily_tick.start()

    def cog_unload(self):
        self._daily_tick.cancel()

    # ── Background task ────────────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def _daily_tick(self):
        now_utc = datetime.now(timezone.utc)

        for guild in self.bot.guilds:
            try:
                await self._maybe_remind(guild, now_utc)
            except Exception as exc:
                log.error("Reminder failed for guild %s (%d): %s", guild.name, guild.id, exc, exc_info=True)

    @_daily_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    async def _maybe_remind(self, guild: discord.Guild, now_utc: datetime):
        settings = await get_guild_settings(guild.id)

        channel_id = settings.get("reminder_channel")
        if not channel_id:
            return  # reminders not configured for this guild

        tz_name = settings.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            log.warning("Invalid timezone '%s' for guild %d — skipping.", tz_name, guild.id)
            return

        now_local = now_utc.astimezone(tz)

        # Fire only during the 1 PM hour, and only once per day
        if now_local.hour != _REMINDER_HOUR:
            return

        today_str = now_local.strftime("%Y-%m-%d")
        if settings.get("last_reminder") == today_str:
            return  # already sent today

        channel = guild.get_channel(channel_id)
        if channel is None:
            log.warning("Reminder channel %d not found in guild %d — clearing config.", channel_id, guild.id)
            await set_reminder_channel(guild.id, None)
            return

        # Permission check before attempting to send
        me = guild.me
        if me:
            perms = channel.permissions_for(me)
            if not (perms.send_messages and perms.embed_links):
                log.warning(
                    "Missing send/embed perms in channel %d (guild %d) — skipping reminder.",
                    channel_id, guild.id,
                )
                return

        await self._send_reminder(guild, channel, now_local)
        await mark_reminder_sent(guild.id, today_str)
        log.info("Reminder sent to guild '%s' (%d) in channel #%s.", guild.name, guild.id, channel.name)

    async def _send_reminder(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        now_local: datetime,
    ):
        fact = get_fact_for(now_local.month, now_local.day)
        leaderboard = await get_leaderboard(guild.id, limit=3)
        player_ids  = await get_active_player_ids(guild.id)

        embed = _build_reminder_embed(now_local, fact, leaderboard)
        await channel.send(embed=embed)

        if not player_ids:
            return

        # Paginate mentions so no single message exceeds Discord's 2000-char limit
        chunks = _chunk_mentions(player_ids)
        prefix = "👆 **Tag, you're it!** Today's challenge is waiting:"
        for i, chunk in enumerate(chunks):
            content = f"{prefix}\n{chunk}" if i == 0 else chunk
            await channel.send(content)

    # ── /remind channel ────────────────────────────────────────────────────────

    @remind.command(
        name="channel",
        description="Set the channel where daily reminders are sent (admin only)",
    )
    @discord.option("channel", discord.TextChannel, description="Channel to receive daily reminders")
    async def remind_channel(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        if not await self._admin_only(ctx):
            return

        # Verify bot can actually post there
        me = ctx.guild.me if ctx.guild else None
        if me:
            perms = channel.permissions_for(me)
            if not (perms.send_messages and perms.embed_links):
                await ctx.respond(
                    f"⚠️ I don't have **Send Messages** and **Embed Links** permissions in {channel.mention}.\n"
                    "Please fix the permissions first, then run this command again.",
                    ephemeral=True,
                )
                return

        await set_reminder_channel(ctx.guild_id, channel.id)
        await ctx.respond(
            f"✅ Daily reminders will be posted in {channel.mention} at **1:00 PM** server time.\n"
            f"Use `/remind timezone` to set the correct timezone (current: `{(await get_guild_settings(ctx.guild_id))['timezone']}`).",
            ephemeral=True,
        )

    # ── /remind timezone ───────────────────────────────────────────────────────

    @remind.command(
        name="timezone",
        description="Set the server timezone for daily reminders (admin only)",
    )
    @discord.option(
        "tz",
        str,
        description="IANA timezone name, e.g. America/New_York, Europe/London, Asia/Kolkata",
    )
    async def remind_timezone(self, ctx: discord.ApplicationContext, tz: str):
        if not await self._admin_only(ctx):
            return

        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            await ctx.respond(
                f"❌ `{tz}` is not a valid timezone.\n"
                "Use an IANA name like `America/New_York`, `Europe/London`, or `Asia/Kolkata`.\n"
                "Full list: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>",
                ephemeral=True,
            )
            return

        await set_guild_timezone(ctx.guild_id, tz)
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(tz))
        await ctx.respond(
            f"✅ Server timezone set to **{tz}**.\n"
            f"Current local time: **{now_local.strftime('%H:%M')}** — "
            f"reminders fire daily at **13:00** ({now_local.strftime('%Z')}).",
            ephemeral=True,
        )

    # ── /remind off ────────────────────────────────────────────────────────────

    @remind.command(name="off", description="Disable daily reminders for this server (admin only)")
    async def remind_off(self, ctx: discord.ApplicationContext):
        if not await self._admin_only(ctx):
            return
        await set_reminder_channel(ctx.guild_id, None)
        await ctx.respond("🔕 Daily reminders disabled for this server.", ephemeral=True)

    # ── /remind status ─────────────────────────────────────────────────────────

    @remind.command(name="status", description="Show current reminder configuration")
    async def remind_status(self, ctx: discord.ApplicationContext):
        if not ctx.guild:
            await ctx.respond("This command only works in a server.", ephemeral=True)
            return
        settings = await get_guild_settings(ctx.guild_id)
        channel_id = settings.get("reminder_channel")
        tz_name    = settings.get("timezone") or "UTC"
        last_sent  = settings.get("last_reminder") or "never"

        channel_str = f"<#{channel_id}>" if channel_id else "not set"

        try:
            tz = ZoneInfo(tz_name)
            now_str = datetime.now(timezone.utc).astimezone(tz).strftime("%H:%M %Z")
        except ZoneInfoNotFoundError:
            now_str = "unknown (invalid timezone)"

        embed = discord.Embed(
            title="🔔 Daily Reminder Status",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Channel",      value=channel_str,              inline=True)
        embed.add_field(name="Timezone",     value=f"`{tz_name}`",           inline=True)
        embed.add_field(name="Fires at",     value="13:00 (1 PM) local",     inline=True)
        embed.add_field(name="Current time", value=now_str,                  inline=True)
        embed.add_field(name="Last sent",    value=last_sent,                inline=True)
        embed.add_field(
            name="Players tagged",
            value=str(len(await get_active_player_ids(ctx.guild_id))),
            inline=True,
        )
        embed.set_footer(text="Use /remind channel and /remind timezone to configure.")
        await ctx.respond(embed=embed, ephemeral=True)

    # ── /remind test ───────────────────────────────────────────────────────────

    @remind.command(name="test", description="Send a test reminder right now (admin only)")
    async def remind_test(self, ctx: discord.ApplicationContext):
        if not await self._admin_only(ctx):
            return

        settings = await get_guild_settings(ctx.guild_id)
        channel_id = settings.get("reminder_channel")
        if not channel_id:
            await ctx.respond(
                "⚠️ No reminder channel set. Use `/remind channel` first.",
                ephemeral=True,
            )
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.respond("⚠️ Reminder channel no longer exists. Please reconfigure.", ephemeral=True)
            return

        tz_name = settings.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")

        await ctx.respond("📨 Sending test reminder…", ephemeral=True)
        now_local = datetime.now(timezone.utc).astimezone(tz)
        await self._send_reminder(ctx.guild, channel, now_local)

    # ── Helper ─────────────────────────────────────────────────────────────────

    async def _admin_only(self, ctx: discord.ApplicationContext) -> bool:
        if not ctx.guild:
            await ctx.respond("This command only works in a server.", ephemeral=True)
            return False
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.respond(
                "⚠️ You need **Manage Server** permission to configure reminders.",
                ephemeral=True,
            )
            return False
        return True


def setup(bot: discord.Bot):
    bot.add_cog(ReminderCog(bot))
