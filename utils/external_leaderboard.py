"""
External leaderboard integration.

Configuration (via environment variables):
  EXTERNAL_LEADERBOARDS       Comma-separated names of services, e.g. "SIGMAFEUD,NAVI"

For each service name X:
  X_ENABLED                   "true"/"1"/"yes" to activate (default: false)
  X_URL                       Base URL of the service, e.g. https://sigmafeud-production.up.railway.app
  X_API_KEY                   Bearer token for the service
  X_GUILD_IDS                 (optional) Comma-separated Discord guild IDs that should report
                              to this service. Omit or leave empty to report from ALL guilds.

Example .env snippet:
  EXTERNAL_LEADERBOARDS=SIGMAFEUD,NAVI
  SIGMAFEUD_ENABLED=true
  SIGMAFEUD_URL=https://sigmafeud-production.up.railway.app
  SIGMAFEUD_API_KEY=your_key_here
  NAVI_ENABLED=false
  NAVI_URL=https://navi.example.com
  NAVI_API_KEY=your_navi_key
  SIGMAFEUD_GUILD_IDS=123456789,987654321
"""
import asyncio
import logging
import os

import aiohttp

log = logging.getLogger("sigmonions.leaderboard")

_services: list[dict] | None = None


def _load_services() -> list[dict]:
    raw = os.getenv("EXTERNAL_LEADERBOARDS", "").strip()
    if not raw:
        return []

    result = []
    for name in raw.split(","):
        name = name.strip().upper()
        if not name:
            continue
        enabled = os.getenv(f"{name}_ENABLED", "false").strip().lower() in ("1", "true", "yes")
        if not enabled:
            continue
        url = os.getenv(f"{name}_URL", "").strip().rstrip("/")
        api_key = os.getenv(f"{name}_API_KEY", "").strip()
        if not url or not api_key:
            log.warning(
                "External leaderboard %s is enabled but %s_URL or %s_API_KEY is missing — skipping.",
                name, name, name,
            )
            continue
        guild_ids_raw = os.getenv(f"{name}_GUILD_IDS", "").strip()
        guild_ids: set[int] = (
            {int(g.strip()) for g in guild_ids_raw.split(",") if g.strip().isdigit()}
            if guild_ids_raw else set()
        )
        result.append({"name": name, "url": url, "api_key": api_key, "guild_ids": guild_ids})
        log.info(
            "External leaderboard registered: %s  guilds=%s",
            name,
            "all" if not guild_ids else sorted(guild_ids),
        )
    return result


def _get_services() -> list[dict]:
    global _services
    if _services is None:
        _services = _load_services()
    return _services


async def _post_to_service(
    service: dict,
    user_id: int,
    guild_id: int,
    username: str,
    points: int,
    match_id: str | None,
) -> None:
    headers = {"Authorization": f"Bearer {service['api_key']}"}
    payload = {
        "user_id":  user_id,
        "guild_id": guild_id,
        "username": username,
        "points":   points,
        "game_id":  match_id,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{service['url']}/api/v1/points",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                log.info(
                    "%s ← user=%s guild=%s points=%+d → %s",
                    service["name"], user_id, guild_id, points, data,
                )
    except Exception as exc:
        log.warning(
            "External leaderboard %s failed for user=%s guild=%s: %s",
            service["name"], user_id, guild_id, exc,
        )


async def report_points(
    user_id: int,
    guild_id: int,
    username: str,
    points: int,
    match_id: str | None = None,
) -> None:
    """Fire-and-forget: send earned points to all enabled external leaderboard services."""
    tasks = [
        _post_to_service(svc, user_id, guild_id, username, points, match_id)
        for svc in _get_services()
        if not svc["guild_ids"] or guild_id in svc["guild_ids"]
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
