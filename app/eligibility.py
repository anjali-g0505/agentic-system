"""Chargeback eligibility. Pure, deterministic Python over the rules row, the transaction row and case history.

The LLM never decides eligibility, never computes a date and never states a figure this module did not return.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from app.rails import rail_for


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Eligibility:
    rrn: str
    claim_type: str
    decision: str  # ELIGIBLE | NOT_ELIGIBLE | REFER_TO_RISK
    reason_code: str | None
    checks: list[Check] = field(default_factory=list)
    days_elapsed: int | None = None
    chargeback_window_days: int | None = None
    chargeback_deadline: str | None = None
    representment_window_days: int | None = None
    required_evidence: list[str] = field(default_factory=list)
    prior_cases: list[dict] = field(default_factory=list)
    rule_source: dict = field(default_factory=dict)
    rail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _txn_date(txn: dict) -> date:
    return datetime.fromisoformat(txn["txn_timestamp"]).date()


def evaluate(txn: dict, rule: dict | None, claim_type: str, cases: list[dict], today: date) -> Eligibility:
    rail = rail_for(txn["funding_source"])
    if rule is None:
        return Eligibility(rrn=txn["rrn"], claim_type=claim_type, decision="NOT_ELIGIBLE", reason_code=None,
                           checks=[Check("claim_type_known", False, f"No rule row for claim_type '{claim_type}'")],
                           rail=rail)

    checks: list[Check] = []
    funds = rule["eligible_funding_sources"].split("|")
    checks.append(Check("rail", txn["funding_source"] in funds,
                        f"Funding source {txn['funding_source']} ({rail['rail_label']}); rule allows {', '.join(funds)}"
                        + ("" if txn["funding_source"] in funds else
                           ". Not a RuPay card chargeback: route through the UPI dispute process instead")))
    if rule["required_txn_type"] != "any":
        checks.append(Check("txn_type", txn["txn_type"] == rule["required_txn_type"],
                            f"Transaction type {txn['txn_type']}; rule requires {rule['required_txn_type']}"))
    checks.append(Check("status", txn["status"] == rule["required_status"],
                        f"Transaction status {txn['status']}; rule requires {rule['required_status']}"))
    want = rule["required_credited_to_merchant"]
    if want != "any":
        got = (txn["credited_to_merchant"] or "").lower()
        checks.append(Check("merchant_credit", got == want,
                            f"credited_to_merchant={got or 'n/a'}; rule requires {want}"))

    window = int(rule["chargeback_window_days"])
    txn_day = _txn_date(txn)
    elapsed = (today - txn_day).days
    deadline = txn_day + timedelta(days=window)
    checks.append(Check("time_limit", elapsed <= window,
                        f"{elapsed} days since transaction on {txn_day.isoformat()}; window {window} days "
                        f"(last day {deadline.isoformat()})"))

    open_cases = [c for c in cases if c["status"] == "RAISED"]
    withdrawn = [c for c in cases if c["status"] == "WITHDRAWN"]
    checks.append(Check("no_open_case", not open_cases,
                        "No open chargeback on this RRN" if not open_cases else
                        f"Open chargeback already exists: {', '.join(c['case_id'] for c in open_cases)}"))
    checks.append(Check("not_previously_withdrawn", not withdrawn,
                        "No case on this RRN was withdrawn by risk" if not withdrawn else
                        "Previously withdrawn by risk supervisor: " + "; ".join(
                            f"{c['case_id']} by {c['withdrawn_by']} on {str(c['withdrawn_at'])[:10]} "
                            f"({c['withdrawn_reason']})" for c in withdrawn)))

    route_to_human = rule["route_to_human"].lower() == "true"
    if route_to_human:
        checks.append(Check("fraud_routing", False,
                            "Suspected fraud / not recognised: must be decided by a risk supervisor, not the agent"))
        decision = "REFER_TO_RISK"
    else:
        decision = "ELIGIBLE" if all(c.passed for c in checks) else "NOT_ELIGIBLE"

    provisional = rule["source_note"].strip().upper() == "VERIFY" or not rule["source_circular"].strip()
    return Eligibility(
        rrn=txn["rrn"], claim_type=claim_type, decision=decision, reason_code=rule["reason_code"], checks=checks,
        days_elapsed=elapsed, chargeback_window_days=window, chargeback_deadline=deadline.isoformat(),
        representment_window_days=int(rule["representment_window_days"]),
        required_evidence=rule["required_evidence"].split("|"),
        prior_cases=[{k: str(v) if v is not None else None for k, v in c.items()
                      if k in ("case_id", "status", "claim_type", "raised_by", "withdrawn_by", "withdrawn_reason",
                               "withdrawn_at", "created_at")} for c in cases],
        rule_source={
            "file": "chargeback_rules.csv", "claim_type": claim_type,
            "source_circular": rule["source_circular"] or None, "source_page": rule["source_page"] or None,
            "provisional": provisional,
            "note": ("PROVISIONAL: rule figures are not yet verified against a circular (source_note=VERIFY); "
                     "do not present them as circular-backed") if provisional else rule["source_note"],
        },
        rail=rail,
    )
