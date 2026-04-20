import os
import json
import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "sigmonions.db"


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id       INTEGER NOT NULL,
                guild_id      INTEGER NOT NULL,
                username      TEXT,
                games_played  INTEGER DEFAULT 0,
                rounds_played INTEGER DEFAULT 0,
                groups_found  INTEGER DEFAULT 0,
                correct_guesses INTEGER DEFAULT 0,
                wrong_guesses   INTEGER DEFAULT 0,
                total_points    INTEGER DEFAULT 0,
                best_game_points INTEGER DEFAULT 0,
                current_streak  INTEGER DEFAULT 0,
                best_streak     INTEGER DEFAULT 0,
                perfect_rounds  INTEGER DEFAULT 0,
                first_finds     INTEGER DEFAULT 0,
                fastest_group_ms INTEGER,
                last_played     TEXT,
                PRIMARY KEY (user_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS server_stats (
                guild_id             INTEGER PRIMARY KEY,
                games_played         INTEGER DEFAULT 0,
                rounds_played        INTEGER DEFAULT 0,
                total_points_awarded INTEGER DEFAULT 0,
                server_streak        INTEGER DEFAULT 0,
                best_server_streak   INTEGER DEFAULT 0,
                most_active_user_id  INTEGER,
                last_active          TEXT
            );

            CREATE TABLE IF NOT EXISTS round_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER,
                channel_id   INTEGER,
                game_id      TEXT,
                round_num    INTEGER,
                categories   TEXT,
                participants TEXT,
                scores_json  TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS game_history (
                game_id      TEXT PRIMARY KEY,
                guild_id     INTEGER,
                channel_id   INTEGER,
                started_by   INTEGER,
                total_rounds INTEGER,
                final_scores TEXT,
                winner_id    INTEGER,
                started_at   TEXT,
                ended_at     TEXT
            );
        """)
        await db.commit()


_ALLOWED_USER_STAT_COLS: frozenset[str] = frozenset({
    "username", "games_played", "rounds_played", "groups_found",
    "correct_guesses", "wrong_guesses", "total_points", "best_game_points",
    "current_streak", "best_streak", "perfect_rounds", "first_finds",
    "fastest_group_ms", "last_played",
})


async def get_user_stats(user_id: int, guild_id: int, username: str = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
            (user_id, guild_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            await db.execute(
                "INSERT OR IGNORE INTO user_stats (user_id, guild_id, username) VALUES (?,?,?)",
                (user_id, guild_id, username),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
                (user_id, guild_id),
            ) as cur:
                row = await cur.fetchone()

        return dict(row)


async def update_user_stats(user_id: int, guild_id: int, **kwargs):
    if not kwargs:
        return
    unknown = set(kwargs) - _ALLOWED_USER_STAT_COLS
    if unknown:
        raise ValueError(f"update_user_stats: unknown column(s): {unknown}")
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id, guild_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE user_stats SET {sets} WHERE user_id=? AND guild_id=?", vals
        )
        await db.commit()


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

    new_streak = (stats["current_streak"] + 1) if won_streak else 0
    best_streak = max(stats["best_streak"], new_streak)
    best_game   = max(stats["best_game_points"], game_points)

    new_fastest = stats["fastest_group_ms"]
    if fastest_ms is not None:
        new_fastest = fastest_ms if new_fastest is None else min(new_fastest, fastest_ms)

    from datetime import datetime, timezone
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT username, total_points, games_played, groups_found,
                      best_game_points, best_streak, correct_guesses, wrong_guesses,
                      perfect_rounds, first_finds
               FROM user_stats
               WHERE guild_id=? AND games_played > 0
               ORDER BY total_points DESC LIMIT ?""",
            (guild_id, limit),
        ) as cur:
            rows = await cur.fetchall()
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
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO round_history
               (guild_id, channel_id, game_id, round_num, categories, participants, scores_json, completed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                guild_id, channel_id, game_id, round_num,
                json.dumps(categories),
                json.dumps(participants),
                json.dumps(scores),
                now,
            ),
        )
        await db.commit()


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
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO game_history
               (game_id, guild_id, channel_id, started_by, total_rounds, final_scores, winner_id, started_at, ended_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                game_id, guild_id, channel_id, started_by, total_rounds,
                json.dumps(final_scores), winner_id, started_at, now,
            ),
        )
        await db.commit()


async def get_server_stats(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_stats WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT OR IGNORE INTO server_stats (guild_id) VALUES (?)", (guild_id,)
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM server_stats WHERE guild_id=?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
    return dict(row)


async def increment_server_stats(guild_id: int, rounds: int = 0, points: int = 0):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO server_stats (guild_id, games_played, rounds_played, total_points_awarded, last_active)
               VALUES (?,1,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 games_played=games_played+1,
                 rounds_played=rounds_played+?,
                 total_points_awarded=total_points_awarded+?,
                 last_active=?""",
            (guild_id, rounds, points, now, rounds, points, now),
        )
        await db.commit()
