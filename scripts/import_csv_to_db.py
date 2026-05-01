"""
Import categories and words from the CSV file into the PostgreSQL database.

Usage:
    python scripts/import_csv_to_db.py

Safe to run multiple times — uses ON CONFLICT DO NOTHING so existing data is
preserved and only new categories/words are inserted.
"""
import asyncio
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import asyncpg

CSV_PATH = ROOT / "data" / "categorized_words_phrases.csv"


def _parse_words(raw: str) -> list[str]:
    cleaned = raw.strip().lstrip("[").rstrip("]")
    return [w.strip() for w in cleaned.split(",") if w.strip()]


async def run():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    print(f"Connecting to database…")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)

    # Ensure tables exist
    async with pool.acquire() as conn:
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

    cats_inserted = 0
    cats_skipped = 0
    words_inserted = 0
    words_skipped = 0

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat_name = row["category"].strip()
            words = _parse_words(row["words_and_phrases"])

            if len(words) < 4:
                print(f"  SKIP  {cat_name!r} — fewer than 4 words ({len(words)})")
                cats_skipped += 1
                continue

            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Upsert category (do nothing on conflict = skip if already exists)
                    existing_id = await conn.fetchval(
                        "SELECT id FROM categories WHERE name=$1", cat_name
                    )
                    if existing_id is None:
                        cat_id = await conn.fetchval(
                            "INSERT INTO categories (name) VALUES ($1) RETURNING id", cat_name
                        )
                        cats_inserted += 1
                        print(f"  NEW   {cat_name!r}  ({len(words)} words)")
                    else:
                        cat_id = existing_id
                        cats_skipped += 1
                        print(f"  EXIST {cat_name!r}  (id={cat_id})")

                    for word in words:
                        tag = await conn.execute(
                            "INSERT INTO category_words (category_id, word) VALUES ($1, $2) "
                            "ON CONFLICT (category_id, word) DO NOTHING",
                            cat_id, word,
                        )
                        if tag == "INSERT 0 1":
                            words_inserted += 1
                        else:
                            words_skipped += 1

    await pool.close()

    print()
    print("─" * 50)
    print(f"Categories : {cats_inserted} inserted, {cats_skipped} already existed")
    print(f"Words      : {words_inserted} inserted, {words_skipped} already existed")
    print("Done!")


if __name__ == "__main__":
    asyncio.run(run())
