"""All /sigmonion commands — game, stats, and leaderboard."""
import asyncio
import logging
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
    if pts >= 200:
        return discord.Color.gold()
    if pts >= 100:
        return discord.Color.green()
    if pts > 0:
        return discord.Color.blurple()
    return discord.Color.red()


class SigmonionCog(commands.Cog):

    sigmonion = discord.SlashCommandGroup(
        "sigmonion",
        "Sigmonions — the multiplayer word-grouping game",
    )

    def __init__(self, bot: discord.Bot):
        self.bot    = bot
        self.engine = GameEngine()
        self._games: dict[int, GameSession] = {}   # channel_id → GameSession

    # ── internal helpers ───────────────────────────────────────────────────────

    def _get_session(self, channel_id: int) -> GameSession | None:
        s = self._games.get(channel_id)
        return s if s and s.status == "active" else None

    async def _respond(self, ctx: discord.ApplicationContext, **kwargs):
        if ctx.response.is_done():
            await ctx.followup.send(**kwargs)
        else:
            await ctx.respond(**kwargs)

    def _board_embed(self, session: GameSession, rnd, title: str) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=self.engine.format_board(rnd),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=(
                f"Round {rnd.round_num}/{session.total_rounds}  ·  "
                f"Groups found: {len(rnd.found_order)}/4  ·  "
                "/sigmonion guess letters:abcd"
            )
        )
        return embed

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
                f"<@{uid}>: {correct}✅ {wrong}❌ acc={acc} fastest={_fmt_ms(fastest)}"
            )
        return "\n".join(lines) if lines else "*No data*"

    async def _persist_game(self, session: GameSession, aborted: bool = False):
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
            for uid, pts in session.scores.items():
                await upsert_user_after_game(
                    user_id=uid,
                    guild_id=session.guild_id,
                    username=session._usernames.get(uid, str(uid)),  # type: ignore[attr-defined]
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

    # ── round management ───────────────────────────────────────────────────────

    async def _start_next_round(self, channel: discord.TextChannel, session: GameSession):
        session.current_round_num += 1
        rnd = self.engine.build_round(session.current_round_num)
        session.rounds.append(rnd)

        embed = discord.Embed(
            title=f"🎮 Round {rnd.round_num}/{session.total_rounds} — Sigmonions",
            description=self.engine.format_board(rnd),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use /sigmonion guess letters:abcd  ·  /sigmonion board to refresh")
        msg = await channel.send(embed=embed)
        rnd.board_message_id = msg.id

    async def _finish_round(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        rnd,
    ):
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
            cd_msg = await channel.send(
                embed=discord.Embed(
                    description=f"⏳ Next round starts in **{ROUND_COUNTDOWN}s**...",
                    color=discord.Color.blurple(),
                )
            )
            await asyncio.sleep(ROUND_COUNTDOWN)
            try:
                await cd_msg.delete()
            except discord.NotFound:
                pass
            await self._start_next_round(channel, session)

    async def _finish_game(self, channel: discord.TextChannel, session: GameSession):
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
            title="🎉 Game Over!",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Per-player breakdown",
            value=self._build_stats_snapshot(session),
            inline=False,
        )
        await channel.send(embed=embed)
        await self._persist_game(session)

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
        await ctx.defer()

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
        # Attach username cache so persist can look up names
        session._usernames: dict[int, str] = {}  # type: ignore[attr-defined]
        session._usernames[ctx.author.id] = str(ctx.author)
        self._games[ctx.channel_id] = session

        embed = discord.Embed(
            title="🎮 Sigmonions — Game Starting!",
            description=(
                f"**{rounds} round{'s' if rounds > 1 else ''}** · Started by <@{ctx.author.id}>\n\n"
                "Find groups of **4 words** that share a hidden category.\n"
                "Type `/sigmonion guess letters:abcd` to submit a group.\n\n"
                f"✅ Correct: **+{POINTS_CORRECT} pts**  "
                f"❌ Wrong: **{POINTS_WRONG} pts**\n"
                f"⚡ Speed bonuses · 🔥 Streak bonuses · ⭐ Perfect round: **+{POINTS_PERFECT_ROUND} pts**"
            ),
            color=discord.Color.green(),
        )
        await ctx.followup.send(embed=embed)
        await asyncio.sleep(2)
        await self._start_next_round(ctx.channel, session)

    # ── /sigmonion guess ───────────────────────────────────────────────────────

    @sigmonion.command(name="guess", description="Guess 4 letters that form a group (e.g. abcd)")
    async def guess(
        self,
        ctx: discord.ApplicationContext,
        letters: discord.Option(str, "4 letters from the board, e.g. abcd"),  # type: ignore[valid-type]
    ):
        await ctx.defer()

        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.followup.send(
                "No game is running here. Use `/sigmonion play` to start one.",
                ephemeral=True,
            )
            return

        rnd = session.current_round
        if rnd is None or rnd.is_complete():
            await ctx.followup.send("The current round is already complete.", ephemeral=True)
            return

        user_id  = ctx.author.id
        username = str(ctx.author)
        # Cache username for persistence
        if not hasattr(session, "_usernames"):
            session._usernames = {}  # type: ignore[attr-defined]
        session._usernames[user_id] = username  # type: ignore[attr-defined]

        result = self.engine.process_guess(session, user_id, username, letters.strip())

        # Hard validation error
        if result.get("error") and result["group_idx"] is None:
            await ctx.followup.send(f"⚠️ {result['error']}", ephemeral=True)
            return

        channel = ctx.channel

        if not result["valid"]:
            embed = discord.Embed(
                title="❌ Wrong grouping!",
                description=f"**{POINTS_WRONG} pts** deducted from <@{user_id}>",
                color=discord.Color.red(),
            )
            embed.set_footer(text="The words remain on the board — keep trying!")
            await ctx.followup.send(embed=embed)
            return

        # Correct guess
        group     = rnd.groups[result["group_idx"]]
        color_pos = len(rnd.found_order) - 1
        emoji     = GROUP_COLORS[color_pos]
        breakdown = "  ·  ".join(result["breakdown"])

        embed = discord.Embed(
            title=f"{emoji} Correct! — {group.category}",
            description=(
                f"**{', '.join(group.words)}**\n\n"
                f"<@{user_id}> earned **{result['points_earned']:+} pts**\n"
                f"_{breakdown}_"
            ),
            color=_score_color(result["points_earned"]),
        )
        await ctx.followup.send(embed=embed)

        if result["round_complete"]:
            auto_idx = result["auto_reveal_group"]
            if auto_idx is not None:
                ag = rnd.groups[auto_idx]
                auto_embed = discord.Embed(
                    title=f"{GROUP_COLORS[3]} Auto-revealed — {ag.category}",
                    description=f"**{', '.join(ag.words)}**",
                    color=discord.Color.purple(),
                )
                await channel.send(embed=auto_embed)
            await asyncio.sleep(1)
            await self._finish_round(channel, session, rnd)
        else:
            # Update board in-place
            remaining = 4 - len(rnd.found_order)
            updated = discord.Embed(
                title=f"🎮 Round {rnd.round_num}/{session.total_rounds} — {remaining} group{'s' if remaining != 1 else ''} left",
                description=self.engine.format_board(rnd),
                color=discord.Color.blurple(),
            )
            updated.set_footer(text="/sigmonion guess letters:abcd  ·  /sigmonion board")
            if rnd.board_message_id:
                try:
                    board_msg = await channel.fetch_message(rnd.board_message_id)
                    await board_msg.edit(embed=updated)
                except discord.NotFound:
                    msg = await channel.send(embed=updated)
                    rnd.board_message_id = msg.id
            else:
                msg = await channel.send(embed=updated)
                rnd.board_message_id = msg.id

    # ── /sigmonion board ───────────────────────────────────────────────────────

    @sigmonion.command(name="board", description="Re-show the current game board")
    async def board(self, ctx: discord.ApplicationContext):
        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.respond("No game running here. Start one with `/sigmonion play`.", ephemeral=True)
            return
        rnd = session.current_round
        if rnd is None:
            await ctx.respond("No round in progress.", ephemeral=True)
            return
        remaining = 4 - len(rnd.found_order)
        embed = self._board_embed(
            session, rnd,
            f"🎮 Round {rnd.round_num}/{session.total_rounds} — {remaining} group{'s' if remaining != 1 else ''} left",
        )
        await ctx.respond(embed=embed)

    # ── /sigmonion scores ──────────────────────────────────────────────────────

    @sigmonion.command(name="scores", description="Show current game scores")
    async def scores(self, ctx: discord.ApplicationContext):
        session = self._get_session(ctx.channel_id)
        if not session:
            await ctx.respond("No game running here.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"🏆 Scores — Round {session.current_round_num}/{session.total_rounds}",
            color=discord.Color.gold(),
        )
        if not session.scores:
            embed.description = "*No scores yet — start guessing!*"
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
        await self._persist_game(session, aborted=True)

    # ── /sigmonion help ────────────────────────────────────────────────────────

    @sigmonion.command(name="help", description="How to play Sigmonions")
    async def help(self, ctx: discord.ApplicationContext):
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
            name="Commands",
            value=(
                "`/sigmonion play [rounds]` — Start a game (default 5, max 20)\n"
                "`/sigmonion guess letters:abcd` — Submit a 4-letter group guess\n"
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
                f"✅ Correct group **+{POINTS_CORRECT}**  ·  ❌ Wrong guess **{POINTS_WRONG}**\n"
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

        correct  = data["correct_guesses"]
        wrong    = data["wrong_guesses"]
        avg_pts  = data["total_points"] // max(data["games_played"], 1)
        gpg      = data["groups_found"] / max(data["rounds_played"], 1)

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
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            acc   = _accuracy(r["correct_guesses"], r["wrong_guesses"])
            name  = r["username"] or f"Player {i+1}"
            if sort_by == "accuracy":
                metric = acc
            elif sort_by == "groups_found":
                metric = f"{r['groups_found']} groups"
            elif sort_by == "best_game":
                metric = f"{r['best_game_points']:+} pts"
            elif sort_by == "streak":
                metric = f"{r['best_streak']} 🔥"
            else:
                metric = f"{r['total_points']:+} pts"
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
            insights = []
            top_pts  = max(rows, key=lambda r: r["total_points"])
            top_acc  = max(
                rows,
                key=lambda r: r["correct_guesses"] / max(r["correct_guesses"] + r["wrong_guesses"], 1),
            )
            top_str  = max(rows, key=lambda r: r["best_streak"])
            top_perf = max(rows, key=lambda r: r["perfect_rounds"])
            top_fast = min(
                (r for r in rows if r["fastest_group_ms"] is not None),  # type: ignore[arg-type]
                key=lambda r: r.get("fastest_group_ms") or 999999,
                default=None,
            )

            insights.append(f"🏆 Top scorer: **{top_pts['username']}** ({top_pts['total_points']:+} pts)")
            insights.append(f"🎯 Most accurate: **{top_acc['username']}** ({_accuracy(top_acc['correct_guesses'], top_acc['wrong_guesses'])})")
            insights.append(f"🔥 Longest streak: **{top_str['username']}** ({top_str['best_streak']} games)")
            if top_perf["perfect_rounds"] > 0:
                insights.append(f"⭐ Perfect rounds king: **{top_perf['username']}** ({top_perf['perfect_rounds']})")
            if top_fast:
                insights.append(f"⚡ Speed demon: **{top_fast['username']}** (fastest group: {_fmt_ms(top_fast.get('fastest_group_ms'))})")

            embed.add_field(name="🔍 Insights", value="\n".join(insights), inline=False)

        if data["last_active"]:
            embed.set_footer(text=f"Last active: {data['last_active'][:10]}")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(SigmonionCog(bot))
