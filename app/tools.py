"""Agent tools. Read tools run directly; write tools run only after a human approves (see app/agent.py).

Every tool goes through rbac.requires_roles: role check, identity from the session, audit row, masked output.
There is deliberately no tool that initiates, reverses or authorises a payment, or that issues a refund.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from psycopg import errors
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app import data
from app.db import pool
from app.eligibility import evaluate
from app.llm import grader
from app.masking import mask_card
from app.rails import products_for_question, rail_for
from app.rbac import ALL_ROLES, CARDHOLDER, ISSUER_OPS, RISK_SUPERVISOR, Principal, requires_roles
from app.retriever import retriever

IST = timezone(timedelta(hours=5, minutes=30))
NO_REFUND_PROMISE = ("A chargeback or dispute is a claim, not a refund: the outcome depends on the acquirer's "
                     "response and no refund is promised.")
NOT_FOUND = "No transaction with that RRN/ARN is visible to you."

WRITE_ROLES = {
    "raise_chargeback": (ISSUER_OPS,),
    "lodge_dispute": (CARDHOLDER,),
    "withdraw_case": (RISK_SUPERVISOR,),
    "refer_to_risk": ALL_ROLES,
}


def today_ist():
    return datetime.now(IST).date()


async def visible_txn(p: Principal, ref: str) -> dict | None:
    txn = await data.get_transaction(ref.strip())
    # Same answer for "does not exist" and "not yours": a cardholder cannot probe other people's RRNs.
    if txn is None or (p.role == CARDHOLDER and txn["cardholder_id"] != p.user_id):
        return None
    return txn


async def cases_for_rrn(rrn: str) -> list[dict]:
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute("SELECT * FROM chargeback_cases WHERE rrn = %s ORDER BY created_at", (rrn,))
        return await cur.fetchall()


async def eligibility_for(txn: dict, claim_type: str):
    return evaluate(txn, await data.get_rule(claim_type), claim_type, await cases_for_rrn(txn["rrn"]), today_ist())


def txn_view(txn: dict, merchant: dict | None) -> dict:
    return {
        "rrn": txn["rrn"], "arn": txn["arn"], "amount_inr": txn["amount"], "status": txn["status"],
        "txn_type": txn["txn_type"], "txn_mode": txn["txn_mode"], "funding_source": txn["funding_source"],
        "card": mask_card(txn["card_last4"]), "txn_timestamp": txn["txn_timestamp"],
        "settlement_date": txn["settlement_date"], "credited_to_merchant": txn["credited_to_merchant"],
        "refund_status": txn["refund_status"], "merchant": merchant["merchant_name"] if merchant else None,
        "payee_vpa": txn["payee_vpa"], "issuer_bank": txn["issuer_bank"], "acquirer_bank": txn["acquirer_bank"],
    }


# --------------------------------------------------------------------------- read tools
class Grade(BaseModel):
    relevant: list[int] = Field(description="Numbers of passages that directly help answer the question")


async def grade(question: str, passages: list) -> list[int] | None:
    listing = "\n\n".join(f"[{i}] {c.content[:900]}" for i, c in enumerate(passages, start=1))
    try:
        out = await grader.with_structured_output(Grade, method="json_schema").ainvoke(
            "You grade retrieved passages from payment-network circulars. Return the numbers of passages that "
            "contain information directly relevant to answering the question. Return an empty list if none do.\n\n"
            f"Question: {question}\n\nPassages:\n{listing}")
    except Exception:
        return None
    return [i for i in out.relevant if 1 <= i <= len(passages)]


async def _search_circulars(p: Principal, *, question: str, rrn: Optional[str], confirmed_by=None) -> dict:
    rail = None
    if rrn:
        txn = await visible_txn(p, rrn)
        if txn is None:
            return {"status": "NOT_FOUND", "message": NOT_FOUND}
        rail = rail_for(txn["funding_source"])
        products = rail["circular_products"]
    else:
        products = products_for_question(question)
    hits = await retriever.search(question, products)
    keep = await grade(question, hits) if hits else []
    if keep is None:
        return {"status": "NOT_VERIFIED", "message": "Relevance grading is unavailable, so no grounded answer "
                "can be given right now. Do not answer from general knowledge."}
    if not keep:
        return {"status": "NOT_COVERED", "rail": rail, "searched_products": products,
                "message": "The circulars in the corpus do not cover this question. Say so; do not answer from "
                           "general knowledge."}
    passages = [{"ref": n, "citation": hits[i - 1].citation(), "text": hits[i - 1].content}
                for n, i in enumerate(keep, start=1)]
    return {"status": "OK", "rail": rail, "searched_products": products, "passages": passages,
            "citations": [x["citation"] | {"ref": x["ref"]} for x in passages],
            "instruction": "Answer only from these passages and cite every statement as [ref]."}


async def _get_transaction_status(p: Principal, *, rrn_or_arn: str, confirmed_by=None) -> dict:
    txn = await visible_txn(p, rrn_or_arn)
    if txn is None:
        return {"status": "NOT_FOUND", "message": NOT_FOUND}
    merchant = await data.get_merchant(txn["merchant_id"]) if txn["merchant_id"] else None
    out = {"status": "OK", "transaction": txn_view(txn, merchant), "rail": rail_for(txn["funding_source"])}
    if txn["response_code"]:
        rc = await data.get_response_code(txn["response_code"])
        out["response_code"] = {
            "code": txn["response_code"],
            "description": rc["description"] if rc else "Unknown code",
            "likely_cause": rc["likely_cause"] if rc else None,
            "customer_guidance": rc["customer_guidance"] if rc else None,
            "source": {"file": "response_codes.csv", "provisional": True if rc is None else rc["source_note"] == "VERIFY",
                       "note": "PROVISIONAL: response code meanings are not yet verified against the NPCI list"},
        }
    if txn["funding_source"] == "RUPAY_CREDIT_CARD" and txn["txn_type"] == "P2P":
        out["flag"] = "RuPay credit card funding a P2P transfer is an exception to the normal rule: review it."
    return out


async def _check_chargeback_eligibility(p: Principal, *, rrn: str, claim_type: str, confirmed_by=None) -> dict:
    txn = await visible_txn(p, rrn)
    if txn is None:
        return {"status": "NOT_FOUND", "message": NOT_FOUND}
    if claim_type not in await data.list_claim_types():
        return {"status": "INVALID", "message": f"Unknown claim_type. Use one of {await data.list_claim_types()}"}
    e = await eligibility_for(txn, claim_type)
    out = {"status": e.decision, "eligibility": e.as_dict(),
           "summary": f"{e.decision} for {claim_type} on {rrn}"}
    if p.role == CARDHOLDER:
        out["note"] = ("Cardholders cannot raise a chargeback; only the issuing bank can. You can lodge a dispute "
                       "with your issuing bank instead.")
    if e.decision == "REFER_TO_RISK":
        out["next_step"] = "Use refer_to_risk so a risk supervisor reviews it. The agent must not resolve fraud."
    return out


async def _list_cases(p: Principal, *, rrn: Optional[str], confirmed_by=None) -> dict:
    where, params = [], []
    if p.role == CARDHOLDER:
        where.append("cardholder_id = %s")
        params.append(p.user_id)
    if rrn:
        where.append("rrn = %s")
        params.append(rrn.strip())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(f"""SELECT case_id, rrn, cardholder_id, reason_code, claim_type, amount, status, raised_by,
                              withdrawn_by, withdrawn_reason, created_at FROM chargeback_cases {clause}
                              ORDER BY created_at DESC LIMIT 50""", params)
        cases = await cur.fetchall()
        await cur.execute(f"""SELECT dispute_id, rrn, cardholder_id, claim_type, status, created_at FROM disputes
                              {clause} ORDER BY created_at DESC LIMIT 50""", params)
        disputes = await cur.fetchall()
        await cur.execute(f"""SELECT referral_id, rrn, cardholder_id, referred_by, status, created_at
                              FROM fraud_referrals {clause} ORDER BY created_at DESC LIMIT 50""", params)
        referrals = await cur.fetchall()
    return {"status": "OK", "chargeback_cases": cases, "disputes": disputes, "fraud_referrals": referrals}


# --------------------------------------------------------------------------- write tools
def _needs_confirmation(confirmed_by: str | None) -> dict | None:
    if not confirmed_by:  # defence in depth: the graph only calls write tools after interrupt() approval
        return {"status": "BLOCKED", "message": "This action requires human confirmation and was not executed."}
    return None


async def _raise_chargeback(p: Principal, *, rrn: str, claim_type: str, confirmed_by=None) -> dict:
    if (blocked := _needs_confirmation(confirmed_by)):
        return blocked
    txn = await visible_txn(p, rrn)
    if txn is None:
        return {"status": "NOT_FOUND", "message": NOT_FOUND}
    key = f"raise:{txn['rrn']}:{claim_type}"
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute("SELECT * FROM chargeback_cases WHERE idempotency_key = %s AND status = 'RAISED'", (key,))
        if (existing := await cur.fetchone()):
            return {"status": "ALREADY_RAISED", "case": existing, "summary": f"replay of {existing['case_id']}",
                    "message": "This chargeback was already raised; returning the original case."}
    e = await eligibility_for(txn, claim_type)
    if e.decision != "ELIGIBLE":
        failed = [c.detail for c in e.checks if not c.passed]
        return {"status": "REFUSED", "decision": e.decision, "failed_checks": failed,
                "summary": f"refused: {'; '.join(failed)}",
                "message": "Chargeback not raised. The cardholder may still lodge a dispute with the issuing bank."}
    case_id = f"CB-{uuid.uuid4().hex[:8].upper()}"
    due = datetime.now(IST) + timedelta(days=e.representment_window_days or 0)
    try:
        async with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """INSERT INTO chargeback_cases (case_id, rrn, cardholder_id, reason_code, claim_type, amount, status,
                   raised_by, raised_by_role, idempotency_key, representment_due_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'RAISED', %s, %s, %s, %s) RETURNING *""",
                (case_id, txn["rrn"], txn["cardholder_id"], e.reason_code, claim_type, data.money(txn["amount"]),
                 p.user_id, p.role, key, due))
            case = await cur.fetchone()
    except errors.UniqueViolation:
        return {"status": "REFUSED", "summary": "refused: concurrent open case",
                "message": "An open chargeback already exists for this RRN (enforced by the database)."}
    return {"status": "RAISED", "case": case, "reason_code": e.reason_code,
            "summary": f"raised {case_id} on {txn['rrn']} under {e.reason_code}",
            "rule_source": e.rule_source, "message": NO_REFUND_PROMISE}


async def _lodge_dispute(p: Principal, *, rrn: str, claim_type: str, description: str, confirmed_by=None) -> dict:
    if (blocked := _needs_confirmation(confirmed_by)):
        return blocked
    txn = await visible_txn(p, rrn)
    if txn is None:
        return {"status": "NOT_FOUND", "message": NOT_FOUND}
    key = f"dispute:{p.user_id}:{txn['rrn']}:{claim_type}"
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """INSERT INTO disputes (dispute_id, rrn, cardholder_id, claim_type, description, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (f"DSP-{uuid.uuid4().hex[:8].upper()}", txn["rrn"], p.user_id, claim_type, description[:1000], key))
            row = await cur.fetchone()
            if row is None:
                await cur.execute("SELECT * FROM disputes WHERE idempotency_key = %s", (key,))
                return {"status": "ALREADY_LODGED", "dispute": await cur.fetchone(),
                        "summary": "replay", "message": "This dispute was already lodged; returning the original."}
            referral = None
            if claim_type == "not_recognised":  # suspected fraud always reaches a human
                await cur.execute(
                    """INSERT INTO fraud_referrals (referral_id, rrn, cardholder_id, referred_by, referred_by_role,
                       note, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO NOTHING RETURNING referral_id""",
                    (f"FR-{uuid.uuid4().hex[:8].upper()}", txn["rrn"], p.user_id, p.user_id, p.role,
                     "Auto-referred: cardholder does not recognise the transaction", f"fraud:{txn['rrn']}:{p.user_id}"))
                referral = await cur.fetchone()
    return {"status": "LODGED", "dispute": row, "fraud_referral": referral,
            "summary": f"dispute {row['dispute_id']} on {txn['rrn']}",
            "message": "Dispute lodged with your issuing bank. The bank decides whether to raise a chargeback. "
                       + NO_REFUND_PROMISE}


async def _withdraw_case(p: Principal, *, case_id: str, reason: str, confirmed_by=None) -> dict:
    if (blocked := _needs_confirmation(confirmed_by)):
        return blocked
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(
            """UPDATE chargeback_cases SET status = 'WITHDRAWN', withdrawn_by = %s, withdrawn_reason = %s,
               withdrawn_at = now(), updated_at = now() WHERE case_id = %s AND status = 'RAISED' RETURNING *""",
            (p.user_id, reason[:500], case_id.strip()))
        row = await cur.fetchone()
        if row is None:
            await cur.execute("SELECT * FROM chargeback_cases WHERE case_id = %s", (case_id.strip(),))
            existing = await cur.fetchone()
            if existing and existing["status"] == "WITHDRAWN":
                return {"status": "ALREADY_WITHDRAWN", "case": existing, "summary": "replay",
                        "message": "This case was already withdrawn; returning the original record."}
            return {"status": "NOT_FOUND", "message": f"No open case {case_id}."}
    return {"status": "WITHDRAWN", "case": row, "summary": f"withdrew {row['case_id']}",
            "message": "Case withdrawn. A new chargeback on this RRN will now be blocked; the cardholder can "
                       "still lodge a dispute."}


async def _refer_to_risk(p: Principal, *, rrn: str, note: str, confirmed_by=None) -> dict:
    if (blocked := _needs_confirmation(confirmed_by)):
        return blocked
    txn = await visible_txn(p, rrn)
    if txn is None:
        return {"status": "NOT_FOUND", "message": NOT_FOUND}
    key = f"fraud:{txn['rrn']}:{p.user_id}"
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(
            """INSERT INTO fraud_referrals (referral_id, rrn, cardholder_id, referred_by, referred_by_role, note,
               idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
            (f"FR-{uuid.uuid4().hex[:8].upper()}", txn["rrn"], txn["cardholder_id"], p.user_id, p.role,
             note[:1000], key))
        row = await cur.fetchone()
        if row is None:
            await cur.execute("SELECT * FROM fraud_referrals WHERE idempotency_key = %s", (key,))
            return {"status": "ALREADY_REFERRED", "referral": await cur.fetchone(), "summary": "replay",
                    "message": "Already referred to the risk team; returning the original referral."}
    return {"status": "REFERRED", "referral": row, "summary": f"referral {row['referral_id']} on {txn['rrn']}",
            "message": "Referred to a risk supervisor for human review. The agent does not decide fraud cases."}


# --------------------------------------------------------------------------- LangChain tool surface
_search = requires_roles(*ALL_ROLES)(_search_circulars)
_status = requires_roles(*ALL_ROLES)(_get_transaction_status)
_elig = requires_roles(*ALL_ROLES)(_check_chargeback_eligibility)
_cases = requires_roles(*ALL_ROLES)(_list_cases)
_raise = requires_roles(*WRITE_ROLES["raise_chargeback"])(_raise_chargeback)
_dispute = requires_roles(*WRITE_ROLES["lodge_dispute"])(_lodge_dispute)
_withdraw = requires_roles(*WRITE_ROLES["withdraw_case"])(_withdraw_case)
_refer = requires_roles(*WRITE_ROLES["refer_to_risk"])(_refer_to_risk)


@tool
async def search_circulars(question: str, config: RunnableConfig, rrn: Optional[str] = None) -> str:
    """Search NPCI RuPay/UPI circulars for operating rules. Pass the transaction's rrn when the question is about a
    specific transaction so the search is limited to that transaction's rail. Returns graded passages with citations,
    or NOT_COVERED when the circulars do not answer the question."""
    return await _search(question=question, rrn=rrn, config=config)


@tool
async def get_transaction_status(rrn_or_arn: str, config: RunnableConfig) -> str:
    """Look up a transaction by RRN (12 digits) or ARN: status, rail, amount, merchant, and for failures the
    response code with its cause."""
    return await _status(rrn_or_arn=rrn_or_arn, config=config)


@tool
async def check_chargeback_eligibility(rrn: str, claim_type: str, config: RunnableConfig) -> str:
    """Deterministically check chargeback eligibility for a transaction. claim_type is one of:
    not_credited_to_merchant, goods_not_received, duplicate_processing, credit_not_processed, not_recognised.
    Returns decision (ELIGIBLE / NOT_ELIGIBLE / REFER_TO_RISK), reason code, time limit, prior cases and evidence."""
    return await _elig(rrn=rrn, claim_type=claim_type, config=config)


@tool
async def list_cases(config: RunnableConfig, rrn: Optional[str] = None) -> str:
    """List chargeback cases, disputes and fraud referrals visible to the current user, optionally for one RRN."""
    return await _cases(rrn=rrn, config=config)


@tool
async def raise_chargeback(rrn: str, claim_type: str, config: RunnableConfig) -> str:
    """Raise a chargeback on a transaction (issuer operations only). Requires human confirmation before it runs."""
    return await _raise(rrn=rrn, claim_type=claim_type, config=config)


@tool
async def lodge_dispute(rrn: str, claim_type: str, description: str, config: RunnableConfig) -> str:
    """Lodge a dispute with the issuing bank (cardholders only). This is not a chargeback. Requires human
    confirmation before it runs."""
    return await _dispute(rrn=rrn, claim_type=claim_type, description=description, config=config)


@tool
async def withdraw_case(case_id: str, reason: str, config: RunnableConfig) -> str:
    """Withdraw an open chargeback case (risk supervisor only). Requires human confirmation before it runs."""
    return await _withdraw(case_id=case_id, reason=reason, config=config)


@tool
async def refer_to_risk(rrn: str, note: str, config: RunnableConfig) -> str:
    """Refer suspected fraud or an unrecognised transaction to a risk supervisor for human review. Requires human
    confirmation before it runs."""
    return await _refer(rrn=rrn, note=note, config=config)


READ_TOOLS = [search_circulars, get_transaction_status, check_chargeback_eligibility, list_cases]
WRITE_TOOLS = {t.name: t for t in (raise_chargeback, lodge_dispute, withdraw_case, refer_to_risk)}
ALL_TOOLS = READ_TOOLS + list(WRITE_TOOLS.values())


# --------------------------------------------------------------------------- confirmation previews
async def preview(name: str, args: dict, p: Principal) -> dict:
    """What the confirmation modal shows. Computed by code, not by the model. blocked=True means the action
    would fail, so the human is not asked to approve it."""
    if p.role not in WRITE_ROLES.get(name, ()):
        return {"blocked": True, "reason": f"Role '{p.role}' is not permitted to {name.replace('_', ' ')}."}
    if name == "withdraw_case":
        async with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM chargeback_cases WHERE case_id = %s", (str(args.get("case_id", "")).strip(),))
            case = await cur.fetchone()
        if case is None or case["status"] != "RAISED":
            return {"blocked": True, "reason": f"No open case {args.get('case_id')}."}
        return {"blocked": False, "title": "Withdraw chargeback case",
                "facts": {"Case": case["case_id"], "RRN": case["rrn"], "Claim": case["claim_type"],
                          "Reason code": case["reason_code"], "Amount (INR)": str(case["amount"]),
                          "Withdrawal reason": args.get("reason")},
                "consequence": "The case is closed and any later chargeback on this RRN will be blocked. "
                               "This cannot be undone."}
    txn = await visible_txn(p, str(args.get("rrn", "")))
    if txn is None:
        return {"blocked": True, "reason": NOT_FOUND}
    merchant = await data.get_merchant(txn["merchant_id"]) if txn["merchant_id"] else None
    base = {"RRN": txn["rrn"], "Amount (INR)": txn["amount"], "Merchant": merchant["merchant_name"] if merchant else "-",
            "Card": mask_card(txn["card_last4"]) or "-", "Date": txn["txn_timestamp"][:10]}
    if name == "raise_chargeback":
        e = await eligibility_for(txn, str(args.get("claim_type", "")))
        if e.decision != "ELIGIBLE":
            return {"blocked": True, "reason": "; ".join(c.detail for c in e.checks if not c.passed),
                    "decision": e.decision}
        return {"blocked": False, "title": "Raise chargeback",
                "facts": base | {"Claim": e.claim_type, "Reason code": e.reason_code,
                                 "Deadline": e.chargeback_deadline, "Evidence": ", ".join(e.required_evidence)},
                "provisional": e.rule_source.get("provisional"), "rule_note": e.rule_source.get("note"),
                "consequence": "A chargeback will be filed against the acquirer. This cannot be undone by the "
                               "agent; only a risk supervisor can withdraw it. " + NO_REFUND_PROMISE}
    if name == "lodge_dispute":
        return {"blocked": False, "title": "Lodge dispute with issuing bank",
                "facts": base | {"Claim": args.get("claim_type"), "Description": args.get("description")},
                "consequence": "A dispute is recorded with your issuing bank. It is not a chargeback. "
                               "This cannot be undone. " + NO_REFUND_PROMISE}
    return {"blocked": False, "title": "Refer to risk supervisor",
            "facts": base | {"Note": args.get("note")},
            "consequence": "A fraud referral is created for human review by a risk supervisor. This cannot be undone."}
