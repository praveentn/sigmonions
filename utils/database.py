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
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id         BIGINT PRIMARY KEY,
                timezone         TEXT   NOT NULL DEFAULT 'UTC',
                reminder_channel BIGINT,
                last_reminder    TEXT
            )
        """)
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS category_words (
                id          SERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                word        TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (category_id, word)
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


# ── Guild settings (reminders) ─────────────────────────────────────────────────

async def get_guild_settings(guild_id: int) -> dict:
    async with _db().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_settings WHERE guild_id=$1", guild_id,
        )
        if row is None:
            await conn.execute(
                "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
                guild_id,
            )
            row = await conn.fetchrow(
                "SELECT * FROM guild_settings WHERE guild_id=$1", guild_id,
            )
    return dict(row)


async def set_guild_timezone(guild_id: int, tz: str) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, timezone)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone""",
            guild_id, tz,
        )


async def set_reminder_channel(guild_id: int, channel_id: int | None) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, reminder_channel)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET reminder_channel = EXCLUDED.reminder_channel""",
            guild_id, channel_id,
        )


async def mark_reminder_sent(guild_id: int, date_str: str) -> None:
    async with _db().acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_settings (guild_id, last_reminder)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET last_reminder = EXCLUDED.last_reminder""",
            guild_id, date_str,
        )


# ── Categories (question bank) ─────────────────────────────────────────────────

async def get_all_categories() -> dict[str, list[str]]:
    """Return {category_name: [words]} for game engine use."""
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.name, cw.word FROM categories c "
            "JOIN category_words cw ON cw.category_id = c.id ORDER BY c.name, cw.word"
        )
    cats: dict[str, list[str]] = {}
    for row in rows:
        cats.setdefault(row["name"], []).append(row["word"])
    return cats


async def get_categories_for_admin() -> list[dict]:
    """Full category data for the admin panel."""
    async with _db().acquire() as conn:
        cats = await conn.fetch("""
            SELECT c.id, c.name, c.updated_at, COUNT(cw.id)::int AS word_count
            FROM categories c
            LEFT JOIN category_words cw ON cw.category_id = c.id
            GROUP BY c.id ORDER BY c.name
        """)
        result = []
        for cat in cats:
            words = await conn.fetch(
                "SELECT id, word FROM category_words WHERE category_id=$1 ORDER BY word",
                cat["id"],
            )
            result.append({
                "id": cat["id"],
                "name": cat["name"],
                "word_count": cat["word_count"],
                "words": [{"id": w["id"], "word": w["word"]} for w in words],
                "updated_at": str(cat["updated_at"]),
            })
    return result


async def create_category(name: str, words: list[str]) -> dict:
    """Create a new category with initial words. Returns the created record."""
    clean_name = name.strip()
    clean_words = [w.strip() for w in words if w.strip()]
    async with _db().acquire() as conn:
        async with conn.transaction():
            cat_id = await conn.fetchval(
                "INSERT INTO categories (name) VALUES ($1) RETURNING id", clean_name
            )
            for word in clean_words:
                await conn.execute(
                    "INSERT INTO category_words (category_id, word) VALUES ($1, $2) "
                    "ON CONFLICT (category_id, word) DO NOTHING",
                    cat_id, word,
                )
    return {"id": cat_id, "name": clean_name, "word_count": len(clean_words), "words": []}


async def update_category_name(category_id: int, new_name: str) -> bool:
    async with _db().acquire() as conn:
        tag = await conn.execute(
            "UPDATE categories SET name=$1, updated_at=NOW() WHERE id=$2",
            new_name.strip(), category_id,
        )
    return tag == "UPDATE 1"


async def add_words_to_category(category_id: int, words: list[str]) -> int:
    """Insert words (skip duplicates). Returns count of newly inserted words."""
    count = 0
    async with _db().acquire() as conn:
        async with conn.transaction():
            for word in words:
                word = word.strip()
                if not word:
                    continue
                tag = await conn.execute(
                    "INSERT INTO category_words (category_id, word) VALUES ($1, $2) "
                    "ON CONFLICT (category_id, word) DO NOTHING",
                    category_id, word,
                )
                if tag == "INSERT 0 1":
                    count += 1
            if count > 0:
                await conn.execute(
                    "UPDATE categories SET updated_at=NOW() WHERE id=$1", category_id
                )
    return count


async def update_word(word_id: int, new_word: str) -> bool:
    async with _db().acquire() as conn:
        tag = await conn.execute(
            "UPDATE category_words SET word=$1 WHERE id=$2", new_word.strip(), word_id
        )
        if tag == "UPDATE 1":
            await conn.execute(
                "UPDATE categories SET updated_at=NOW() "
                "WHERE id=(SELECT category_id FROM category_words WHERE id=$1)",
                word_id,
            )
    return tag == "UPDATE 1"


async def get_active_player_ids(guild_id: int) -> list[int]:
    """Return user_ids of ALL players who have ever played at least one game in this guild.

    This is intentionally all-time, not filtered by recent activity, so the daily
    reminder tags every member who has ever played — not just those who played yesterday.
    """
    async with _db().acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM user_stats WHERE guild_id=$1 AND games_played > 0",
            guild_id,
        )
    return [r["user_id"] for r in rows]
