"""DuckDB over the CSVs, read in place. Every value comes back as text; callers convert (Decimal for money)."""
from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from pathlib import Path

import duckdb

from app.config import settings

TABLES = ("transactions", "cardholders", "merchants", "reason_codes", "chargeback_rules", "response_codes")

_con = duckdb.connect(database=":memory:")
_lock = threading.Lock()
for _t in TABLES:
    _path = (Path(settings.DATA_DIR) / f"{_t}.csv").as_posix()
    # all_varchar: a 12-digit RRN must never become a BIGINT, and money must never become a float.
    _con.execute(f"CREATE VIEW {_t} AS SELECT * FROM read_csv('{_path}', header=true, all_varchar=true)")


def _query(sql: str, params: list | None = None) -> list[dict]:
    with _lock:
        cur = _con.cursor()
        try:
            cur.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [{c: v for c, v in zip(cols, row)} for row in cur.fetchall()]
        finally:
            cur.close()


async def query(sql: str, params: list | None = None) -> list[dict]:
    return await asyncio.to_thread(_query, sql, params)


async def get_transaction(ref: str) -> dict | None:
    rows = await query("SELECT * FROM transactions WHERE rrn = ? OR arn = ?", [ref, ref])
    return rows[0] if rows else None


async def get_rule(claim_type: str) -> dict | None:
    rows = await query("SELECT * FROM chargeback_rules WHERE claim_type = ?", [claim_type])
    return rows[0] if rows else None


async def list_claim_types() -> list[str]:
    return [r["claim_type"] for r in await query("SELECT claim_type FROM chargeback_rules ORDER BY claim_type")]


async def get_response_code(code: str) -> dict | None:
    rows = await query("SELECT * FROM response_codes WHERE code = ?", [code])
    return rows[0] if rows else None


async def get_merchant(merchant_id: str) -> dict | None:
    rows = await query("SELECT * FROM merchants WHERE merchant_id = ?", [merchant_id])
    return rows[0] if rows else None


async def get_user(user_id: str) -> dict | None:
    rows = await query("SELECT cardholder_id, name, role, issuer_bank FROM cardholders WHERE cardholder_id = ?", [user_id])
    return rows[0] if rows else None


async def list_demo_users() -> list[dict]:
    return await query("""SELECT cardholder_id, name, role FROM cardholders
                          WHERE cardholder_id IN ('CH0001', 'CH0017', 'OPS001', 'OPS002', 'SUP001')
                          ORDER BY role, cardholder_id""")


def money(v: str | None) -> Decimal | None:
    return Decimal(v) if v not in (None, "") else None
