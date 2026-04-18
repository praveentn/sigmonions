import logging

import discord
from discord.ext import commands

from utils.database import get_user_stats, get_leaderboard, get_server_stats

log = logging.getLogger("sigmonions.stats")


def _accuracy(correct: int, wrong: int) -> str:
    total = correct + wrong
    return f"{correct / total * 100:.1f}%" if total else "—"


def _fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _rank_medal(rank: int) -> str:
    return ["🥇", "🥈", "🥉"].get(rank, f"#{rank + 1}") if rank < 3 else f"#{rank + 1}"


class StatsCog(commands.Cog):
    sigmonion = discord.SlashCommandGroup(
        "sigmonion",
        "Sigmonions — the Discord word-grouping game",
    )

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    # ── /sigmonion stats ───────────────────────────────────────────────────────

    @sigmonion.command(name="stats", description="View your (or another player's) stats")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "Player to look up (default: you)", required=False) = None,  # type: ignore
    ):
        await ctx.defer()
        target = user or ctx.author

        data = await get_user_stats(target.id, ctx.guild_id, str(target))
        if data["games_played"] == 0:
            await ctx.followup.send(
                f"**{target.display_name}** hasn't played any Sigmonions games yet!\n"
                "Start one with `/sigmonion play`.",
                ephemeral=True,
            )
            return

        correct = data["correct_guesses"]
        wrong   = data["wrong_guesses"]
        total_g = correct + wrong
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
                f"First finds: **{data['first_finds']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Streaks & Bonuses",
            value=(
                f"Current streak: **{data['current_streak']}** 🔥\n"
                f"Best streak: **{data['best_streak']}** 🏅\n"
                f"Perfect rounds: **{data['perfect_rounds']}** ⭐\n"
                f"Fastest group: **{_fmt_ms(data['fastest_group_ms'])}** ⚡"
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
        sort_by: discord.Option(
            str,
            "Sort by which metric",
            choices=["total_points", "groups_found", "accuracy", "best_game", "streak"],
            default="total_points",
        ) = "total_points",  # type: ignore
    ):
        await ctx.defer()

        rows = await get_leaderboard(ctx.guild_id, limit=15)
        if not rows:
            await ctx.followup.send("No one has played yet! Start a game with `/sigmonion play`.")
            return

        # Re-sort based on chosen metric
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

        sort_label = {
            "total_points": "Total Points",
            "groups_found": "Groups Found",
            "accuracy": "Accuracy",
            "best_game": "Best Game Score",
            "streak": "Best Streak",
        }.get(sort_by, "Total Points")

        embed = discord.Embed(
            title=f"🏆 Sigmonions Leaderboard — {sort_label}",
            color=discord.Color.gold(),
        )

        lines = []
        for i, r in enumerate(rows[:10]):
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"`{i+1}.`"
            acc   = _accuracy(r["correct_guesses"], r["wrong_guesses"])
            name  = r["username"] or f"User {i+1}"

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

        # Top-3 spotlight
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

    @sigmonion.command(name="server", description="Server-wide Sigmonions statistics")
    async def server(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        data = await get_server_stats(ctx.guild_id)
        rows = await get_leaderboard(ctx.guild_id, limit=100)

        total_players  = len(rows)
        total_correct  = sum(r["correct_guesses"] for r in rows)
        total_wrong    = sum(r["wrong_guesses"] for r in rows)
        server_acc     = _accuracy(total_correct, total_wrong)
        avg_pts_player = (
            sum(r["total_points"] for r in rows) // max(total_players, 1)
        )

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
            name="🎯 Server Accuracy",
            value=(
                f"Total correct: **{total_correct}**\n"
                f"Total wrong: **{total_wrong}**\n"
                f"Server accuracy: **{server_acc}**\n"
                f"Avg pts/player: **{avg_pts_player:+}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"Server streak: **{data['server_streak']}** 🔥\n"
                f"Best server streak: **{data['best_server_streak']}** 🏅"
            ),
            inline=True,
        )

        if rows:
            top_acc = max(
                rows,
                key=lambda r: r["correct_guesses"] / max(r["correct_guesses"] + r["wrong_guesses"], 1),
            )
            top_pts = rows[0] if rows else None

            insights = []
            if top_pts:
                insights.append(f"🏆 Top scorer: **{top_pts['username']}** ({top_pts['total_points']:+} pts)")
            if top_acc:
                acc_val = _accuracy(top_acc["correct_guesses"], top_acc["wrong_guesses"])
                insights.append(f"🎯 Most accurate: **{top_acc['username']}** ({acc_val})")

            top_streak = max(rows, key=lambda r: r["best_streak"])
            insights.append(f"🔥 Longest streak: **{top_streak['username']}** ({top_streak['best_streak']} games)")

            top_perfect = max(rows, key=lambda r: r["perfect_rounds"])
            if top_perfect["perfect_rounds"] > 0:
                insights.append(f"⭐ Most perfect rounds: **{top_perfect['username']}** ({top_perfect['perfect_rounds']})")

            embed.add_field(
                name="🔍 Insights",
                value="\n".join(insights),
                inline=False,
            )

        if data["last_active"]:
            embed.set_footer(text=f"Last active: {data['last_active'][:10]}")

        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(StatsCog(bot))
