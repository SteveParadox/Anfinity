"""Force-refresh the note_contributions materialized view.

Run this after deployment if you want an immediate attribution refresh instead of
waiting for the next background-triggered refresh cycle.
"""

from __future__ import annotations

import asyncio
import sys

from app.services.note_contributions import refresh_note_contributions_materialized_view


async def _main() -> int:
    refreshed = await refresh_note_contributions_materialized_view(force=True)
    if refreshed:
        print("note_contributions refreshed")
    else:
        print("note_contributions refresh skipped or already in progress")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
