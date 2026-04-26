import json
import os
from datetime import datetime, timezone

import asyncpg

_pool: asyncpg.Pool | None = None

_ALLOWED_USER_STAT_COLS: frozenset[str] = frozenset({
    "username", "games_played", "rounds_played", "groups_found",
    "correct_guesses", "wrong_guesses", "total_points", "best_game_points",
    "current_streak", "best_streak", "perfect_rounds", "first_finds",
    "fastest_group_ms", "last_played",
})


def _db() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _pool


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # asyncpg requires postgresql://, Railway exposes postgres://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(_pg_url(), min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id          BIGINT  NOT NULL,
                guild_id         BIGINT  NOT NULL,
                username         TEXT,
                games_played     INTEGER NOT NULL DEFAULT 0,
                rounds_played    INTEGER NOT NULL DEFAULT 0,
                groups_found     INTEGER NOT NULL DEFAULT 0,
                correct_guesses  INTEGER NOT NULL DEFAULT 0,
                wrong_guesses    INTEGER NOT NULL DEFAULT 0,
                total_points     INTEGER NOT NULL DEFAULT 0,
                best_game_points INTEGER NOT NULL DEFAULT 0,
                current_streak   INTEGER NOT NULL DEFAULT 0,
                best_streak      INTEGER NOT NULL DEFAULT 0,
                perfect_rounds   INTEGER NOT NULL DEFAULT 0,
                first_finds      INTEGER NOT NULL DEFAULT 0,
                fastest_group_ms INTEGER,
                last_played      TEXT,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS server_stats (
                guild_id             BIGINT  PRIMARY KEY,
                games_played         INTEGER NOT NULL DEFAULT 0,
                rounds_played        INTEGER NOT NULL DEFAULT 0,
                total_points_awarded INTEGER NOT NULL DEFAULT 0,
                server_streak        INTEGER NOT NULL DEFAULT 0,
                best_server_streak   INTEGER NOT NULL DEFAULT 0,
                most_active_user_id  BIGINT,
                last_active          TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS round_history (
                id           SERIAL  PRIMARY KEY,
                guild_id     BIGINT,
                channel_id   BIGINT,
                game_id      TEXT,
                round_num    INTEGER,
                categories   TEXT,
                participants TEXT,
                scores_json  TEXT,
                completed_at TEXT,
                UNIQUE (game_id, round_num)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_history (
                game_id      TEXT PRIMARY KEY,
                guild_id     BIGINT,
                channel_id   BIGINT,
                started_by   BIGINT,
                total_rounds INTEGER,
                final_scores TEXT,
                winner_id    BIGINT,
                started_at   TEXT,
                ended_at     TEXT
            )
        """)


async def get_user_stats(user_id: int, guild_id: int, username: str = None) -> dict:
    async with _db().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_stats WHERE user_id=$1 AND guild_id=$2",
            user_id, guild_id,
        )
        if row is None:
            await conn.execute(
                """INSERT INTO user_stats (user_id, guild_id, username)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (user_id, guild_id) DO NOTHING""",
                user_id, guild_id, username,
            )
            row = await conn.fetchrow(
                "SELECT * FROM user_stats WHERE user_id=$1 AND guild_id=$2",
                user_id, guild_id,
            )
        return dict(row)


async def update_user_stats(user_id: int, guild_id: int, **kwargs):
    if not kwargs:
        return
    unknown = set(kwargs) - _ALLOWED_USER_STAT_COLS
    if unknown:
        raise ValueError(f"update_user_stats: unknown column(s): {unknown}")
    # $1..$N are the column values; $N+1 and $N+2 are the WHERE params
    sets = ", ".join(f"{col}=${i + 1}" for i, col in enumerate(kwargs))
    n = len(kwargs)
    params = [*kwargs.values(), user_id, guild_id]
    async with _db().acquire() as conn:
        await conn.execute(
            f"UPDATE user_stats SET {sets} WHERE user_id=${n + 1} AND guild_id=${n + 2}",
            *params,
        )


async def upsert_user_after_game(
    user_id: int,
    guild_id: int,
    username: str,
    game_points: int,
    groups_found: int,
    correct_guesses: int,
    wrong_guesses: int,
    rounds_played: int,
    perfect_rounds: int,
    first_finds: int,
    fastest_ms: int | None,
    won_streak: bool,
):
    stats = await get_user_stats(user_id, guild_id, username)

    new_streak  = (stats["current_streak"] + 1) if won_streak else 0
    best_streak = max(stats["best_streak"], new_streak)
    best_game   = max(stats["best_game_points"], game_points)

    new_fastest = stats["fastest_group_ms"]
    if fastest_ms is not None:
        new_fastest = fastest_ms if new_fastest is None else min(new_fastest, fastest_ms)

    now = datetime.now(timezone.utc).isoformat()

    await update_user_stats(
        user_id, guild_id,
        username=username,
        games_played=stats["games_played"] + 1,
        rounds_played=stats["rounds_played"] + rounds_played,
        groups_found=stats["groups_found"] + groups_found,
        correct_guesses=stats["correct_guesses"] + correct_guesses,
        wrong_guesses=stats["wrong_guesses"] + wrong_guesses,
        total_points=stats["total_points"] + game_points,
        best_game_points=best_game,
        current_streak=new_streak,
        best_streak=best_streak,
        perfect_rounds=stats["perfect_rounds"] + perfect_rounds,
        first_finds=stats["first_finds"] + first_finds,
        fastest_group_ms=new_fastest,
        last_played=now,
    )


async def get_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            """SELECT username, total_points, games_played, groups_found,
                      best_game_points, best_streak, correct_guesses, wrong_guesses,
                      perfect_rounds, first_finds
               FROM user_stats
               WHERE guild_id=$1 AND games_played > 0
               ORDER BY total_points DESC LIMIT $2""",
            guild_id, limit,
        )
    return [dict(r) for r in rows]


async def save_round_history(
    guild_id: int,
    channel_id: int,
    game_id: str,
    round_num: int,
    categories: list[str],
    participants: list[int],
    scores: dict,
):
    now = datetime.now(timezone.utc).isoformat()
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO round_history
               (guild_id, channel_id, game_id, round_num,
                categories, participants, scores_json, completed_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (game_id, round_num) DO NOTHING""",
            guild_id, channel_id, game_id, round_num,
            json.dumps(categories),
            json.dumps(participants),
            json.dumps(scores),
            now,
        )


async def save_game_history(
    game_id: str,
    guild_id: int,
    channel_id: int,
    started_by: int,
    total_rounds: int,
    final_scores: dict,
    winner_id: int | None,
    started_at: str,
):
    now = datetime.now(timezone.utc).isoformat()
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO game_history
               (game_id, guild_id, channel_id, started_by, total_rounds,
                final_scores, winner_id, started_at, ended_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (game_id) DO UPDATE SET
                 final_scores = EXCLUDED.final_scores,
                 winner_id    = EXCLUDED.winner_id,
                 ended_at     = EXCLUDED.ended_at""",
            game_id, guild_id, channel_id, started_by, total_rounds,
            json.dumps(final_scores), winner_id, started_at, now,
        )


async def get_server_stats(guild_id: int) -> dict:
    async with _db().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM server_stats WHERE guild_id=$1", guild_id,
        )
        if row is None:
            await conn.execute(
                "INSERT INTO server_stats (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
                guild_id,
            )
            row = await conn.fetchrow(
                "SELECT * FROM server_stats WHERE guild_id=$1", guild_id,
            )
    return dict(row)


async def increment_server_stats(guild_id: int, rounds: int = 0, points: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO server_stats
               (guild_id, games_played, rounds_played, total_points_awarded, last_active)
               VALUES ($1, 1, $2, $3, $4)
               ON CONFLICT (guild_id) DO UPDATE SET
                 games_played         = server_stats.games_played + 1,
                 rounds_played        = server_stats.rounds_played + $2,
                 total_points_awarded = server_stats.total_points_awarded + $3,
                 last_active          = $4""",
            guild_id, rounds, points, now,
        )
