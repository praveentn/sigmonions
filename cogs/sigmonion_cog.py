"""All /sigmonion commands plus on_message guess handler."""
import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.database import (
    get_user_stats,
    get_leaderboard,
    get_server_stats,
    save_round_history,
    save_game_history,
    upsert_user_after_game,
    increment_server_stats,
    get_all_categories,
)
from utils.game_engine import (
    GameEngine,
    GameSession,
    GROUP_COLORS,
    POINTS_CORRECT,
    POINTS_WRONG,
    POINTS_PERFECT_ROUND,
)

log = logging.getLogger("sigmonions.game")

MAX_ROUNDS      = 20
DEFAULT_ROUNDS  = 5
ROUND_COUNTDOWN = 5

# ── Flavor text pools ─────────────────────────────────────────────────────────
_CORRECT = [
    "Nailed it! 🎯", "Big brain energy! 🧠", "You're on fire! 🔥",
    "That's the one! 💥", "Genius move! 🧩", "Unstoppable! 🚀",
    "Chef's kiss! 😘", "Dialed in! 📡", "Incredible! ✨", "Too easy! 😎",
]
_WRONG = [
    "Not quite! 😅", "Hmm, think again! 🤔", "Nope! 🙅",
    "Yikes! Try again! 💀", "So wrong it hurts! 😬",
    "Nice try… not! 😏", "Back to the drawing board! 🗑️",
    "Almost! (Just kidding, not even close) 😂", "Keep at it! 💪",
]
_ONE_AWAY = [
    "🟡 **ONE AWAY!** You were SO close — just one word doesn't belong!",
    "🟡 **One away!** Agonisingly close — swap one letter!",
    "🟡 **So close!** Three of those four share a group — find the odd one out!",
]
_STREAK = [
    "🔥 **Streak!** {user} is on a roll — {n} in a row!",
    "🔥 **{n}-streak!** {user} can't be stopped!",
    "🔥 **Hot streak!** {user} has found {n} groups in a row — is anyone keeping up?",
]
_FIRST_FIND = [
    "⚡ **First find!** {user} spotted it first!",
    "⚡ **{user} got there first!** Speed bonus incoming!",
    "⚡ **Quickest draw!** {user} found it before anyone else!",
]
_ROUND_INTROS = [
    "🎯 New round, new groups — good luck!", "🧩 Can you crack the code?",
    "🔍 Study the board carefully...", "🎲 Let the guessing begin!",
    "🧠 Brain cells, activate!", "🕵️ The words hold secrets — find them!",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _accuracy(correct: int, wrong: int) -> str:
    total = correct + wrong
    return f"{correct / total * 100:.1f}%" if total else "—"

def _fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"

def _score_color(pts: int) -> discord.Color:
    if pts >= 200: return discord.Color.gold()
    if pts >= 100: return discord.Color.green()
    if pts > 0:    return discord.Color.blurple()
    return discord.Color.red()

def _is_guess(content: str) -> bool:
    """True if the message looks like a 4-letter board guess."""
    c = content.strip().lower().replace(" ", "")
    return bool(re.fullmatch(r"[a-p]{4}", c))


class GameOverView(discord.ui.View):
    """Persistent action buttons shown at the end of every game."""

    def __init__(self, cog: "SigmonionCog", guild_id: int):
        super().__init__(timeout=600)
        self.cog      = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.success, emoji="▶")
    async def play_again(self, button: discord.ui.Button, interaction: discord.Interaction):
        channel = interaction.channel
        if self.cog._get_session(channel.id):
            await interaction.response.send_message(
                "⚠️ A game is already running here! Use `/sigmonion stop` to end it first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        session = GameSession(
            channel_id=channel.id,
            guild_id=interaction.guild_id,
            started_by=interaction.user.id,
            total_rounds=DEFAULT_ROUNDS,
            started_at=_now_iso(),
        )
        session._usernames = {interaction.user.id: str(interaction.user)}  # type: ignore[attr-defined]
        self.cog._games[channel.id] = session
        start_embed = discord.Embed(
            title="🎮 Sigmonions — Starting!",
            description=(
                f"**{DEFAULT_ROUNDS} rounds**  ·  Started by <@{interaction.user.id}>\n\n"
                "**How to play:** Find groups of **4 words** that share a hidden category.\n"
                "**Just type 4 letters** in chat to guess — no slash command needed!\n\n"
                f"✅ Correct group **+{POINTS_CORRECT} pts**  ·  "
                f"❌ Wrong guess **{POINTS_WRONG} pts**\n"
                f"⚡ Speed bonuses  ·  🔥 Streak bonuses  ·  "
                f"⭐ Perfect round **+{POINTS_PERFECT_ROUND} pts**"
            ),
            color=discord.Color.green(),
        )
        await channel.send(embed=start_embed)
        await self.cog._start_next_round(channel, session)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary, emoji="🏆")
    async def leaderboard(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await get_leaderboard(self.guild_id, limit=10)
        if not rows:
            await interaction.followup.send("No leaderboard data yet!", ephemeral=True)
            return
        embed = discord.Embed(title="🏆 Leaderboard — Total Points", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows[:10]):
            medal   = medals[i] if i < 3 else f"`{i+1}.`"
            correct = r["correct_guesses"]
            wrong   = r["wrong_guesses"]
            acc     = _accuracy(correct, wrong)
            lines.append(f"{medal} **{r['username']}** — {r['total_points']:+} pts  _(acc {acc})_")
        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="My Stats", style=discord.ButtonStyle.secondary, emoji="📊")
    async def my_stats(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await get_user_stats(interaction.user.id, self.guild_id, str(interaction.user))
        if data["games_played"] == 0:
            await interaction.followup.send(
                "You haven't played yet — click **Play Again** to start!", ephemeral=True
            )
            return
        correct = data["correct_guesses"]
        wrong   = data["wrong_guesses"]
        embed   = discord.Embed(title=f"📊 Stats — {interaction.user.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name="🎮 Games",
            value=(
                f"Played: **{data['games_played']}**\n"
                f"Total pts: **{data['total_points']:+}**\n"
                f"Best game: **{data['best_game_points']:+}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎯 Accuracy",
            value=(
                f"Correct: **{correct}**\n"
                f"Wrong: **{wrong}**\n"
                f"Accuracy: **{_accuracy(correct, wrong)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"Best streak: **{data['best_streak']}** 🔥\n"
                f"Perfect rounds: **{data['perfect_rounds']}** ⭐\n"
                f"Fastest group: **{_fmt_ms(data['fastest_group_ms'])}**"
            ),
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class SigmonionCog(commands.Cog):

    sigmonion = discord.SlashCommandGroup(
        "sigmonion",
        "Sigmonions — the multiplayer word-grouping game",
    )

    def __init__(self, bot: discord.Bot):
        self.bot    = bot
        self.engine = GameEngine()
        self._games: dict[int, GameSession] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        cats = await get_all_categories()
        if cats:
            self.engine.set_categories(cats)
            log.info("Loaded %d categories from DB.", len(cats))
        else:
            log.info("DB has no categories yet — using CSV fallback.")

    async def reload_categories(self):
        """Refresh the engine's category cache from the DB (called after admin edits)."""
        cats = await get_all_categories()
        self.engine.set_categories(cats)
        log.info("Game engine categories reloaded (%d categories).", len(cats))

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _guild_only(self, ctx: discord.ApplicationContext) -> bool:
        """Respond with an error and return False when called outside a guild."""
        if ctx.guild is None:
            await ctx.respond(
                "Sigmonions can only be used inside a server, not in DMs.",
                ephemeral=True,
            )
            return False
        return True

    def _get_session(self, channel_id: int) -> GameSession | None:
        s = self._games.get(channel_id)
        return s if s and s.status == "active" else None

    def _make_board_embed(self, session: GameSession, rnd) -> discord.Embed:
        """
        Build a clean 2-column board embed:
        - Description: found groups (one per line with colour emoji)
        - Two inline fields: remaining words split left / right, one word per line
        """
        found_count     = len(rnd.found_order)
        remaining_count = 4 - found_count
        groups_label    = f"{remaining_count} group{'s' if remaining_count != 1 else ''} to find"

        embed = discord.Embed(
            title=f"🎮  Round {rnd.round_num} / {session.total_rounds}  —  {groups_label}",
            color=discord.Color.blurple(),
        )

        # Found groups section
        found_text = self.engine.found_groups_text(rnd)
        if found_text:
            embed.description = found_text

        # Remaining words in two inline columns
        left_col, right_col = self.engine.remaining_columns(rnd)
        if left_col:
            embed.add_field(name="\u200b", value=left_col,  inline=True)
            embed.add_field(name="\u200b", value=right_col, inline=True)
            # Third blank inline field forces 2-col layout on wide screens
            embed.add_field(name="\u200b", value="\u200b",  inline=True)

        embed.set_footer(text=(
            f"Round {rnd.round_num}/{session.total_rounds}  ·  "
            f"{found_count}/4 found  ·  "
            "Type 4 letters to guess, e.g.  abcd"
        ))
        return embed

    async def _repost_board(self, channel: discord.TextChannel, session: GameSession, rnd):
        """Delete the old board message and post a fresh one at the bottom of chat."""
        if rnd.board_message_id:
            try:
                old = await channel.fetch_message(rnd.board_message_id)
                await old.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            rnd.board_message_id = None

        embed = self._make_board_embed(session, rnd)
        msg   = await channel.send(embed=embed)
        rnd.board_message_id  = msg.id
        rnd.board_last_posted = time.time()  # type: ignore[attr-defined]

    def _format_scores_inline(self, session: GameSession) -> str:
        if not session.scores:
            return "*No scores yet*"
        medals = ["🥇", "🥈", "🥉"]
        parts = [
            f"{medals[i] if i < 3 else f'{i+1}.'} <@{uid}> {pts:+}"
            for i, (uid, pts) in enumerate(
                sorted(session.scores.items(), key=lambda x: x[1], reverse=True)
            )
        ]
        return "  ·  ".join(parts)

    def _build_stats_snapshot(self, session: GameSession) -> str:
        lines = []
        for uid in session.scores:
            correct = session.correct_counts.get(uid, 0)
            wrong   = session.wrong_counts.get(uid, 0)
            total   = correct + wrong
            acc     = f"{correct/total*100:.0f}%" if total else "—"
            fastest = session.fastest_ms.get(uid)
            lines.append(
                f"<@{uid}>: {correct}✅ {wrong}❌  acc={acc}  fastest={_fmt_ms(fastest)}"
            )
        return "\n".join(lines) if lines else "*No data*"


    async def _persist_game(self, session: GameSession):
        try:
            winner_id = session.winner()
            total_pts = sum(session.scores.values())
            await save_game_history(
                game_id=session.game_id,
                guild_id=session.guild_id,
                channel_id=session.channel_id,
                started_by=session.started_by,
                total_rounds=session.total_rounds,
                final_scores={str(k): v for k, v in session.scores.items()},
                winner_id=winner_id,
                started_at=session.started_at,
            )
            await increment_server_stats(
                guild_id=session.guild_id,
                rounds=session.current_round_num,
                points=max(total_pts, 0),
            )
            usernames: dict = getattr(session, "_usernames", {})
            for uid, pts in session.scores.items():
                await upsert_user_after_game(
                    user_id=uid,
                    guild_id=session.guild_id,
                    username=usernames.get(uid, str(uid)),
                    game_points=pts,
                    groups_found=session.groups_found_counts.get(uid, 0),
                    correct_guesses=session.correct_counts.get(uid, 0),
                    wrong_guesses=session.wrong_counts.get(uid, 0),
                    rounds_played=session.current_round_num,
                    perfect_rounds=session.perfect_round_counts.get(uid, 0),
                    first_finds=session.first_find_counts.get(uid, 0),
                    fastest_ms=session.fastest_ms.get(uid),
                    won_streak=(pts > 0),
                )
        except Exception as exc:
            log.error("Failed to persist game %s: %s", session.game_id, exc, exc_info=True)

    # ── Round management ───────────────────────────────────────────────────────

    async def _start_next_round(self, channel: discord.TextChannel, session: GameSession):
        session.current_round_num += 1
        session._msg_since_board = 0  # type: ignore[attr-defined]
        rnd = self.engine.build_round(session.current_round_num)
        session.rounds.append(rnd)

        # Intro banner (separate from board so it scrolls away naturally)
        intro = discord.Embed(
            description=f"*{random.choice(_ROUND_INTROS)}*",
            color=discord.Color.og_blurple(),
        )
        intro.set_author(name=f"Round {rnd.round_num} / {session.total_rounds} — Sigmonions")
        await channel.send(embed=intro)

        # Board as its own message so we can always re-post it at the bottom
        await self._repost_board(channel, session, rnd)

    async def _finish_round(self, channel: discord.TextChannel, session: GameSession, rnd):
        summary = discord.Embed(
            title=f"📊 Round {rnd.round_num} Complete!",
            description=self.engine.build_round_summary(rnd, session.scores),
            color=discord.Color.gold(),
        )
        summary.add_field(
            name="Scores so far",
            value=self._format_scores_inline(session),
            inline=False,
        )
        await channel.send(embed=summary)

        await save_round_history(
            guild_id=session.guild_id,
            channel_id=session.channel_id,
            game_id=session.game_id,
            round_num=rnd.round_num,
            categories=[g.category for g in rnd.groups],
            participants=list(rnd.round_scores.keys()),
            scores={str(k): v for k, v in session.scores.items()},
        )

        if session.current_round_num >= session.total_rounds:
            await asyncio.sleep(2)
            await self._finish_game(channel, session)
        else:
            # Animated countdown
            cd_msg = await channel.send(
                embed=discord.Embed(
                    description=f"⏳ Next round in **{ROUND_COUNTDOWN}**...",
                    color=discord.Color.blurple(),
                )
            )
            for t in range(ROUND_COUNTDOWN - 1, 0, -1):
                await asyncio.sleep(1)
                try:
                    await cd_msg.edit(
                        embed=discord.Embed(
                            description=f"⏳ Next round in **{t}**...",
                            color=discord.Color.blurple(),
                        )
                    )
                except discord.NotFound:
                    break
            await asyncio.sleep(1)
            try:
                await cd_msg.delete()
            except discord.NotFound:
                pass
            if session.status != "active":
                return
            await self._start_next_round(channel, session)

    async def _finish_game(self, channel: discord.TextChannel, session: GameSession):
        if session.status != "active":
            return
        session.status = "completed"
        self._games.pop(channel.id, None)

        winner_id = session.winner()
        lines     = ["**Final Results**\n"]
        medals    = ["🥇", "🥈", "🥉"]
        for i, (uid, pts) in enumerate(
            sorted(session.scores.items(), key=lambda x: x[1], reverse=True)
        ):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} <@{uid}> — **{pts:+} pts**")

        if winner_id:
            lines.append(f"\n🏆 **Winner: <@{winner_id}>** — Congratulations!")

        embed = discord.Embed(
            title="🎉 Game Over — Sigmonions!",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Per-player breakdown",
            value=self._build_stats_snapshot(session),
            inline=False,
        )
        view = GameOverView(self, session.guild_id)
        await channel.send(embed=embed, view=view)
        await self._persist_game(session)

    # ── on_message guess handler ───────────────────────────────────────────────

    # How many non-guess messages trigger an automatic board re-post
    _REPOST_THRESHOLD = 10

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return

        session = self._get_session(message.channel.id)
        if not session:
            return

        rnd = session.current_round
        if rnd is None or rnd.is_complete():
            return

        content = message.content.strip().lower().replace(" ", "")
        channel = message.channel

        # ── Non-guess chat message → track activity, maybe auto-repost board ──
        if not _is_guess(content):
            ctr = getattr(session, "_msg_since_board", 0) + 1
            session._msg_since_board = ctr  # type: ignore[attr-defined]
            if ctr >= self._REPOST_THRESHOLD:
                session._msg_since_board = 0  # type: ignore[attr-defined]
                await self._repost_board(channel, session, rnd)
            return

        # ── It's a guess ──────────────────────────────────────────────────────
        session._msg_since_board = 0  # reset on any guess  # type: ignore[attr-defined]

        user_id  = message.author.id
        username = str(message.author)

        if not hasattr(session, "_usernames"):
            session._usernames = {}  # type: ignore[attr-defined]
        session._usernames[user_id] = username  # type: ignore[attr-defined]

        result = self.engine.process_guess(session, user_id, username, content)

        # Hard validation error (already-found letters, duplicate, etc.)
        if result.get("error") and result["group_idx"] is None:
            await message.add_reaction("⚠️")
            await channel.send(f"<@{user_id}> ⚠️ {result['error']}", delete_after=8)
            return

        # ── Wrong guess ───────────────────────────────────────────────────────
        if not result["valid"]:
            one_away = rnd.check_one_away(content)
            await message.add_reaction("❌")
            if one_away:
                await message.add_reaction("🟡")

            embed = discord.Embed(
                title=f"❌  {random.choice(_WRONG)}",
                description=(
                    f"<@{user_id}> **−{abs(POINTS_WRONG)} pts**"
                    + (f"\n\n{random.choice(_ONE_AWAY)}" if one_away else "")
                ),
                color=discord.Color.red(),
            )
            await channel.send(embed=embed, delete_after=15)
            # Board unchanged — no repost needed
            return

        # ── Correct! ──────────────────────────────────────────────────────────
        await message.add_reaction("✅")

        group     = rnd.groups[result["group_idx"]]
        color_pos = len(rnd.found_order) - 1
        emoji     = GROUP_COLORS[color_pos]
        breakdown = "  ·  ".join(result["breakdown"])
        streak    = session.correct_streaks.get(user_id, 0)
        find_pos  = len(rnd.found_order)

        extra_lines = []
        if find_pos == 1:
            extra_lines.append(random.choice(_FIRST_FIND).format(user=f"<@{user_id}>"))
        if streak >= 2:
            extra_lines.append(random.choice(_STREAK).format(user=f"<@{user_id}>", n=streak))
        if any("perfect" in b.lower() for b in result["breakdown"]):
            extra_lines.append(f"⭐ **Perfect round!** +{POINTS_PERFECT_ROUND} bonus pts!")

        embed = discord.Embed(
            title=f"{emoji}  {random.choice(_CORRECT)}  —  {group.category}",
            description=(
                f"> {' · '.join(w.capitalize() for w in group.words)}\n\n"
                f"<@{user_id}>  **+{result['points_earned']} pts**   _{breakdown}_"
                + ("\n\n" + "\n".join(extra_lines) if extra_lines else "")
            ),
            color=_score_color(result["points_earned"]),
        )
        await channel.send(embed=embed)

        if result["round_complete"]:
            auto_idx = result.get("auto_reveal_group")
            if auto_idx is not None:
                ag = rnd.groups[auto_idx]
                auto_embed = discord.Embed(
                    title=f"{GROUP_COLORS[3]}  Auto-revealed  —  {ag.category}",
                    description=f"> {' · '.join(w.capitalize() for w in ag.words)}",
                    color=discord.Color.purple(),
                )
                await channel.send(embed=auto_embed)
            await asyncio.sleep(1)
            await self._finish_round(channel, session, rnd)
        else:
            # Board changed → always repost at the bottom
            await self._repost_board(channel, session, rnd)

    # ── /sigmonion play ────────────────────────────────────────────────────────

    @sigmonion.command(name="play", description="Start a new Sigmonions game")
    async def play(
        self,
        ctx: discord.ApplicationContext,
        rounds: discord.Option(  # type: ignore[valid-type]
            int,
            "Number of rounds (1–20, default 5)",
            min_value=1,
            max_value=MAX_ROUNDS,
            default=DEFAULT_ROUNDS,
        ) = DEFAULT_ROUNDS,
    ):
        if not await self._guild_only(ctx):
            return
        await ctx.defer()

        # Slash commands arrive via interaction webhooks (no channel perms needed),
        # but the game posts regular messages — requires Send Messages + Embed Links.
        me = ctx.guild.me
        if me:
            perms = ctx.channel.permissions_for(me)
            missing = []
            if not perms.send_messages:
                missing.append("Send Messages")
            if not perms.embed_links:
                missing.append("Embed Links")
            if not perms.add_reactions:
                missing.append("Add Reactions")
            if missing:
                await ctx.followup.send(
                    f"⚠️ I'm missing the following permissions in this channel: "
                    f"**{', '.join(missing)}**\n"
                    "Please ask a server admin to grant me these permissions, then try again.",
                    ephemeral=True,
                )
                return

        if self._get_session(ctx.channel_id):
            await ctx.followup.send(
                "⚠️ A game is already running here. Use `/sigmonion stop` to end it first.",
                ephemeral=True,
            )
            return

        session = GameSession(
            channel_id=ctx.channel_id,
            guild_id=ctx.guild_id,
            started_by=ctx.author.id,
            total_rounds=rounds,
            started_at=_now_iso(),
        )
        session._usernames = {ctx.author.id: str(ctx.author)}  # type: ignore[attr-defined]
        self._games[ctx.channel_id] = session

        embed = discord.Embed(
            title="🎮 Sigmonions — Starting!",
            description=(
                f"**{rounds} round{'s' if rounds > 1 else ''}**  ·  "
                f"Started by <@{ctx.author.id}>\n\n"
                "**How to play:** Find groups of **4 words** that share a hidden category.\n"
                "**Just type 4 letters** in chat to guess a group — no slash command needed!\n\n"
                f"✅ Correct group **+{POINTS_CORRECT} pts**  ·  "
                f"❌ Wrong guess **{POINTS_WRONG} pts**\n"
                f"⚡ Speed bonuses  ·  🔥 Streak bonuses  ·  "
                f"⭐ Perfect round **+{POINTS_PERFECT_ROUND} pts**\n\n"
                "🟡 **One away!** shows when 3 of 4 words are right — keep hunting!"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="First round starts in 3 seconds…")
        await ctx.followup.send(embed=embed)

        # Animated "get ready" countdown
        await asyncio.sleep(1)
        ready_msg = await ctx.channel.send("**3…**")
        await asyncio.sleep(1)
        await ready_msg.edit(content="**3… 2…**")
        await asyncio.sleep(1)
        await ready_msg.edit(content="**3… 2… 1… GO! 🚀**")
        await asyncio.sleep(0.5)
        await ready_msg.delete()

        await self._start_next_round(ctx.channel, session)

    # ── /sigmonion board ───────────────────────────────────────────────────────

    @sigmonion.command(name="board", description="Re-pin the board at the bottom of chat")
    async def board(self, ctx: discord.ApplicationContext):
        if not await self._guild_only(ctx):
            return
        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.respond("No game running here. Start one with `/sigmonion play`.", ephemeral=True)
            return
        rnd = session.current_round
        if rnd is None:
            await ctx.respond("No round in progress.", ephemeral=True)
            return
        # Acknowledge the slash command silently, then repost the board
        await ctx.respond("📌 Refreshing board…", ephemeral=True, delete_after=3)
        session._msg_since_board = 0  # type: ignore[attr-defined]
        await self._repost_board(ctx.channel, session, rnd)

    # ── /sigmonion scores ──────────────────────────────────────────────────────

    @sigmonion.command(name="scores", description="Show current game scores")
    async def scores(self, ctx: discord.ApplicationContext):
        if not await self._guild_only(ctx):
            return
        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.respond("No game running here.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"🏆 Scores — Round {session.current_round_num}/{session.total_rounds}",
            color=discord.Color.gold(),
        )
        if not session.scores:
            embed.description = "*No scores yet — type 4 letters to start guessing!*"
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines  = [
                f"{medals[i] if i < 3 else f'{i+1}.'} <@{uid}> — **{pts:+} pts**"
                for i, (uid, pts) in enumerate(
                    sorted(session.scores.items(), key=lambda x: x[1], reverse=True)
                )
            ]
            embed.description = "\n".join(lines)
        await ctx.respond(embed=embed)

    # ── /sigmonion stop ────────────────────────────────────────────────────────

    @sigmonion.command(name="stop", description="Stop the current game (host or admin only)")
    async def stop(self, ctx: discord.ApplicationContext):
        if not await self._guild_only(ctx):
            return
        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.respond("No game running here.", ephemeral=True)
            return
        is_host  = ctx.author.id == session.started_by
        is_admin = bool(ctx.author.guild_permissions.manage_guild) if ctx.guild else False
        if not (is_host or is_admin):
            await ctx.respond("Only the game host or a server admin can stop the game.", ephemeral=True)
            return

        session.status = "completed"
        self._games.pop(ctx.channel_id, None)

        embed = discord.Embed(
            title="🛑 Game Stopped",
            description=f"Stopped by <@{ctx.author.id}>",
            color=discord.Color.red(),
        )
        if session.scores:
            embed.add_field(
                name="Scores at stop",
                value=self._format_scores_inline(session),
                inline=False,
            )
        await ctx.respond(embed=embed)
        await self._persist_game(session)

    # ── /sigmonion help ────────────────────────────────────────────────────────

    @sigmonion.command(name="help", description="How to play Sigmonions")
    async def help(self, ctx: discord.ApplicationContext):
        if not await self._guild_only(ctx):
            return
        embed = discord.Embed(
            title="🎮 How to Play Sigmonions",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Goal",
            value="Find **4 groups of 4 words** that share a hidden category before your friends do!",
            inline=False,
        )
        embed.add_field(
            name="Guessing",
            value=(
                "**Just type 4 letters** directly in chat — no slash command needed!\n"
                "e.g. if you think `a`, `e`, `i`, `o` belong together, type `aeio`\n\n"
                "🟡 **One away!** appears when 3 of your 4 words are correct.\n"
                "✅ Correct  ·  ❌ Wrong  ·  🟡 One away"
            ),
            inline=False,
        )
        embed.add_field(
            name="Commands",
            value=(
                "`/sigmonion play [rounds]` — Start a game (default 5, max 20)\n"
                "`/sigmonion board` — Re-display the board\n"
                "`/sigmonion scores` — Current game scores\n"
                "`/sigmonion stop` — End the game (host/admin)\n"
                "`/sigmonion stats [@user]` — Lifetime stats\n"
                "`/sigmonion leaderboard` — Server leaderboard\n"
                "`/sigmonion server` — Server-wide stats"
            ),
            inline=False,
        )
        embed.add_field(
            name="Scoring",
            value=(
                f"✅ Correct group **+{POINTS_CORRECT}**  ·  ❌ Wrong **{POINTS_WRONG}**\n"
                "⚡ 1st to find: **+50**  ·  2nd: **+30**  ·  3rd: **+10**\n"
                "🔥 Streak (2/3/4+): **+20/+40/+60** per correct\n"
                f"⭐ Perfect round (all 3 groups): **+{POINTS_PERFECT_ROUND}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Colors",
            value="🟨 1st found  ·  🟩 2nd  ·  🟦 3rd  ·  🟪 4th (auto-revealed)",
            inline=False,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    # ── /sigmonion stats ───────────────────────────────────────────────────────

    @sigmonion.command(name="stats", description="View your or another player's stats")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(  # type: ignore[valid-type]
            discord.Member,
            "Player to look up (defaults to you)",
            required=False,
        ) = None,
    ):
        if not await self._guild_only(ctx):
            return
        await ctx.defer()
        target = user or ctx.author
        data   = await get_user_stats(target.id, ctx.guild_id, str(target))

        if data["games_played"] == 0:
            await ctx.followup.send(
                f"**{target.display_name}** hasn't played any Sigmonions games yet!\n"
                "Start one with `/sigmonion play`.",
                ephemeral=True,
            )
            return

        correct = data["correct_guesses"]
        wrong   = data["wrong_guesses"]
        avg_pts = data["total_points"] // max(data["games_played"], 1)
        gpg     = data["groups_found"] / max(data["rounds_played"], 1)

        embed = discord.Embed(
            title=f"📊 Stats — {target.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="🎮 Games",
            value=(
                f"Played: **{data['games_played']}**\n"
                f"Rounds: **{data['rounds_played']}**\n"
                f"Total pts: **{data['total_points']:+}**\n"
                f"Best game: **{data['best_game_points']:+}**\n"
                f"Avg pts/game: **{avg_pts:+}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎯 Accuracy",
            value=(
                f"Correct: **{correct}**\n"
                f"Wrong: **{wrong}**\n"
                f"Accuracy: **{_accuracy(correct, wrong)}**\n"
                f"Groups/round: **{gpg:.2f}**\n"
                f"First finds: **{data['first_finds']}** ⚡"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"Current streak: **{data['current_streak']}** 🔥\n"
                f"Best streak: **{data['best_streak']}** 🏅\n"
                f"Perfect rounds: **{data['perfect_rounds']}** ⭐\n"
                f"Fastest group: **{_fmt_ms(data['fastest_group_ms'])}**"
            ),
            inline=True,
        )
        if data["last_played"]:
            embed.set_footer(text=f"Last played: {data['last_played'][:10]}")
        await ctx.followup.send(embed=embed)

    # ── /sigmonion leaderboard ─────────────────────────────────────────────────

    @sigmonion.command(name="leaderboard", description="Server leaderboard")
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        sort_by: discord.Option(  # type: ignore[valid-type]
            str,
            "Sort metric",
            choices=["total_points", "groups_found", "accuracy", "best_game", "streak"],
            default="total_points",
        ) = "total_points",
    ):
        if not await self._guild_only(ctx):
            return
        await ctx.defer()
        rows = await get_leaderboard(ctx.guild_id, limit=15)
        if not rows:
            await ctx.followup.send(
                "No one has played yet! Start a game with `/sigmonion play`."
            )
            return

        if sort_by == "groups_found":
            rows.sort(key=lambda r: r["groups_found"], reverse=True)
        elif sort_by == "accuracy":
            rows.sort(
                key=lambda r: r["correct_guesses"] / max(r["correct_guesses"] + r["wrong_guesses"], 1),
                reverse=True,
            )
        elif sort_by == "best_game":
            rows.sort(key=lambda r: r["best_game_points"], reverse=True)
        elif sort_by == "streak":
            rows.sort(key=lambda r: r["best_streak"], reverse=True)

        label = {
            "total_points": "Total Points",
            "groups_found": "Groups Found",
            "accuracy":     "Accuracy",
            "best_game":    "Best Game Score",
            "streak":       "Best Streak",
        }.get(sort_by, "Total Points")

        embed = discord.Embed(
            title=f"🏆 Leaderboard — {label}",
            color=discord.Color.gold(),
        )
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, r in enumerate(rows[:10]):
            medal  = medals[i] if i < 3 else f"`{i+1}.`"
            acc    = _accuracy(r["correct_guesses"], r["wrong_guesses"])
            name   = r["username"] or f"Player {i+1}"
            metric = {
                "accuracy":     acc,
                "groups_found": f"{r['groups_found']} groups",
                "best_game":    f"{r['best_game_points']:+} pts",
                "streak":       f"{r['best_streak']} 🔥",
            }.get(sort_by, f"{r['total_points']:+} pts")
            lines.append(f"{medal} **{name}** — {metric}  _(acc {acc})_")
        embed.description = "\n".join(lines)

        if rows:
            top = rows[0]
            embed.add_field(
                name="👑 All-time top player",
                value=(
                    f"**{top['username']}** · {top['total_points']:+} pts · "
                    f"{top['groups_found']} groups · {top['best_streak']}🔥 best streak"
                ),
                inline=False,
            )
        await ctx.followup.send(embed=embed)

    # ── /sigmonion server ──────────────────────────────────────────────────────

    @sigmonion.command(name="server", description="Server-wide Sigmonions stats")
    async def server(self, ctx: discord.ApplicationContext):
        if not await self._guild_only(ctx):
            return
        await ctx.defer()
        data = await get_server_stats(ctx.guild_id)
        rows = await get_leaderboard(ctx.guild_id, limit=100)

        total_players = len(rows)
        total_correct = sum(r["correct_guesses"] for r in rows)
        total_wrong   = sum(r["wrong_guesses"] for r in rows)
        avg_pts       = sum(r["total_points"] for r in rows) // max(total_players, 1)

        embed = discord.Embed(
            title=f"🌐 Server Stats — {ctx.guild.name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📈 Activity",
            value=(
                f"Games played: **{data['games_played']}**\n"
                f"Rounds played: **{data['rounds_played']}**\n"
                f"Points awarded: **{data['total_points_awarded']:,}**\n"
                f"Active players: **{total_players}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎯 Accuracy",
            value=(
                f"Server correct: **{total_correct}**\n"
                f"Server wrong: **{total_wrong}**\n"
                f"Server accuracy: **{_accuracy(total_correct, total_wrong)}**\n"
                f"Avg pts/player: **{avg_pts:+}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"Server streak: **{data['server_streak']}** 🔥\n"
                f"Best ever: **{data['best_server_streak']}** 🏅"
            ),
            inline=True,
        )

        if rows:
            top_pts  = max(rows, key=lambda r: r["total_points"])
            top_acc  = max(rows, key=lambda r: r["correct_guesses"] / max(r["correct_guesses"] + r["wrong_guesses"], 1))
            top_str  = max(rows, key=lambda r: r["best_streak"])
            top_perf = max(rows, key=lambda r: r["perfect_rounds"])
            fast_rows = [r for r in rows if r.get("fastest_group_ms") is not None]
            top_fast = min(fast_rows, key=lambda r: r["fastest_group_ms"], default=None)

            insights = [
                f"🏆 Top scorer: **{top_pts['username']}** ({top_pts['total_points']:+} pts)",
                f"🎯 Most accurate: **{top_acc['username']}** ({_accuracy(top_acc['correct_guesses'], top_acc['wrong_guesses'])})",
                f"🔥 Longest streak: **{top_str['username']}** ({top_str['best_streak']} games)",
            ]
            if top_perf["perfect_rounds"] > 0:
                insights.append(f"⭐ Perfect round king: **{top_perf['username']}** ({top_perf['perfect_rounds']})")
            if top_fast:
                insights.append(f"⚡ Speed demon: **{top_fast['username']}** (fastest: {_fmt_ms(top_fast.get('fastest_group_ms'))})")

            embed.add_field(name="🔍 Insights", value="\n".join(insights), inline=False)

        if data["last_active"]:
            embed.set_footer(text=f"Last active: {data['last_active'][:10]}")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(SigmonionCog(bot))
