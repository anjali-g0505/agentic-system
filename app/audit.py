"""Append-only audit log of every tool call (the table rejects UPDATE/DELETE via trigger)."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.db import pool
from app.masking import scrub

if TYPE_CHECKING:
    from app.rbac import Principal

log = logging.getLogger("audit")


async def record(p: "Principal", tool: str, arguments: dict, outcome: str, summary: str,
                 confirmed_by: str | None) -> None:
    args = scrub(json.dumps(arguments, default=str))
    log.info("tool=%s actor=%s role=%s outcome=%s confirmed_by=%s", tool, p.user_id, p.role, outcome, confirmed_by)
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO audit_log (actor, role, tool, arguments, outcome, result_summary, confirmed_by)
               VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)""",
            (p.user_id, p.role, tool, args, outcome, scrub(summary)[:500], confirmed_by))
