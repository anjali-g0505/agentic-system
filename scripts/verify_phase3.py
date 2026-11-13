"""Phase 3 smoke check: retriever, eligibility on the demo rows, RBAC. Makes no writes except audit rows."""
from __future__ import annotations

import asyncio
import json

from app.db import open_pool, pool
from app.rbac import Principal
from app.retriever import retriever
from app.tools import (check_chargeback_eligibility, get_transaction_status, raise_chargeback, search_circulars,
                       withdraw_case)

CH1, CH17 = Principal("CH0001", "cardholder"), Principal("CH0017", "cardholder")
OPS, SUP = Principal("OPS001", "issuer_ops"), Principal("SUP001", "risk_supervisor")


def cfg(p: Principal, confirmed_by: str | None = None) -> dict:
    return {"configurable": {"principal": p, "confirmed_by": confirmed_by}}


async def main() -> None:
    await open_pool()
    await retriever.load()
    print("schema args (config must be absent):", list(raise_chargeback.args))

    q = "What is the time limit for raising a chargeback on a RuPay transaction?"
    hits = await retriever.search(q, ["RUPAY", "UPI"])
    print(f"\nQUERY: {q}")
    for h in hits[:3]:
        print(f"  - {h.source_filename} p{h.page_number} [{h.product}/{h.content_type}] "
              f"{h.content[:220].replace(chr(10), ' ')}")

    print("\nELIGIBILITY (issuer_ops view):")
    for rrn, claim in [("100000000001", "not_credited_to_merchant"), ("100000000002", "not_credited_to_merchant"),
                       ("100000000003", "not_credited_to_merchant"), ("100000000005", "not_credited_to_merchant"),
                       ("100000000006", "not_recognised"), ("100000000007", "duplicate_processing"),
                       ("100000000010", "goods_not_received")]:
        r = json.loads(await check_chargeback_eligibility.ainvoke({"rrn": rrn, "claim_type": claim}, cfg(OPS)))
        failed = [c["detail"] for c in r["eligibility"]["checks"] if not c["passed"]]
        print(f"  {rrn} {claim:26s} -> {r['status']:13s} {failed[0][:95] if failed else ''}")

    print("\nRBAC:")
    r = json.loads(await get_transaction_status.ainvoke({"rrn_or_arn": "100000000009"}, cfg(CH1)))
    print("  CH0001 reads CH0017's 100000000009 ->", r["status"])
    r = json.loads(await get_transaction_status.ainvoke({"rrn_or_arn": "100000000009"}, cfg(CH17)))
    print("  CH0017 reads own 100000000009    ->", r["status"], r["transaction"]["card"])
    r = json.loads(await raise_chargeback.ainvoke({"rrn": "100000000001", "claim_type": "not_credited_to_merchant"},
                                                  cfg(CH1, confirmed_by="CH0001")))
    print("  cardholder raise_chargeback      ->", r["status"], "|", r["message"])
    r = json.loads(await withdraw_case.ainvoke({"case_id": "CB-SEED-000001", "reason": "x"}, cfg(OPS, "OPS001")))
    print("  issuer_ops withdraw_case         ->", r["status"])
    r = json.loads(await raise_chargeback.ainvoke({"rrn": "100000000001", "claim_type": "not_credited_to_merchant"},
                                                  cfg(OPS)))
    print("  issuer_ops raise w/o confirmation ->", r["status"])
    r = json.loads(await get_transaction_status.ainvoke({"rrn_or_arn": "100000000003"}, cfg(CH1)))
    print("  status 100000000003 ->", r["transaction"]["status"], r["rail"]["rail"], r["response_code"]["code"],
          "-", r["response_code"]["likely_cause"])

    print("\nGROUNDED SEARCH (grader on 20b):")
    for q in ("What is the time limit for raising a chargeback on a RuPay transaction?",
              "What is the capital of France?"):
        r = json.loads(await search_circulars.ainvoke({"question": q}, cfg(OPS)))
        cites = [f"{c['source_filename'][:30]} p{c['page_number']} cl.{c['clause']}" for c in r.get("citations", [])]
        print(f"  {q[:60]:60s} -> {r['status']} {cites[:3]}")

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT tool, actor, outcome FROM audit_log ORDER BY id DESC LIMIT 3")
        print("\naudit_log tail:", await cur.fetchall())


asyncio.run(main())
