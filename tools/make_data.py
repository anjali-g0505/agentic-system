#!/usr/bin/env python3
"""Synthetic data for the RuPay-credit-card-on-UPI chargeback desk prototype.

Standard library only. Seeded and relative to --as-of, so two runs with the
same --as-of are byte-identical and every age (days_ago) holds on any run date.

    python tools/make_data.py --as-of 2026-11-16 --out ./data

Writes transactions.csv, cardholders.csv, merchants.csv, reason_codes.csv,
chargeback_rules.csv, seed_cases.sql and MANIFEST.md. Nothing is written
unless every validation check passes.

All data is invented. No full card numbers are ever generated (card_last4
only); VPAs and mobiles are stored pre-masked.
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import string
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 20261116
IST = timezone(timedelta(hours=5, minutes=30))

N_FILLER_TXN = 190      # + 10 planted = 200
N_CARDHOLDERS = 37      # role=cardholder; + 3 staff = 40
N_MERCHANTS = 25
MAX_DAYS_AGO = 200

# ---------------------------------------------------------------------------
# Column orders (the CSV contract with the DuckDB layer)
# ---------------------------------------------------------------------------
TXN_COLS = [
    "rrn", "arn", "auth_code", "funding_source", "card_last4", "txn_mode", "txn_type",
    "payer_vpa", "payee_vpa", "merchant_id", "mcc", "amount", "mdr_charged", "txn_timestamp",
    "settlement_date", "status", "response_code", "credited_to_merchant", "refund_status",
    "refund_timestamp", "cardholder_id", "issuer_bank", "acquirer_bank",
]
CARDHOLDER_COLS = ["cardholder_id", "name", "mobile_masked", "registered_vpa", "role", "issuer_bank"]
MERCHANT_COLS = ["merchant_id", "merchant_name", "mcc", "category", "acquirer_bank", "onboarded_date"]
REASON_COLS = ["code", "category", "description", "raised_by", "source_circular"]
RULE_COLS = [
    "claim_type", "reason_code", "chargeback_window_days", "representment_window_days",
    "required_evidence", "penalty_per_day", "penalty_starts_after_days",
    "eligible_funding_sources", "required_txn_type", "required_status",
    "required_credited_to_merchant", "route_to_human",
    "source_circular", "source_page", "source_note",
]
RESPONSE_COLS = ["code", "rail", "status_class", "description", "likely_cause", "customer_guidance",
                 "source_circular", "source_note"]

# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------
# (merchant_id, name, mcc, category, acquirer_bank, onboarded_days_ago)
MERCHANTS = [
    ("MER0001", "Annapurna Fresh Mart", 5411, "grocery", "HDFC Bank", 1460),
    ("MER0002", "Kesar Kirana Stores", 5411, "grocery", "Axis Bank", 980),
    ("MER0003", "Sahyadri Highway Fuels", 5541, "fuel", "State Bank of India", 2100),
    ("MER0004", "Dhruv Electronics Hub", 5732, "electronics", "HDFC Bank", 1825),
    ("MER0005", "Vidyut Gadget World", 5732, "electronics", "Kotak Mahindra Bank", 720),
    ("MER0006", "Jeevan Rekha Pharmacy", 5912, "pharmacy", "ICICI Bank", 1100),
    ("MER0007", "Narmada Auto Fuel Point", 5542, "fuel", "Bank of Baroda", 1340),
    ("MER0008", "Sanjeevani Medicos", 5912, "pharmacy", "Axis Bank", 870),
    ("MER0009", "Pravas Holidays", 4722, "travel", "HDFC Bank", 560),
    ("MER0010", "Prakash Power Distribution", 4900, "utilities", "State Bank of India", 1960),
    ("MER0011", "Hariyali Organic Basket", 5411, "grocery", "ICICI Bank", 640),
    ("MER0012", "Godavari Supermarket", 5411, "grocery", "Bank of Baroda", 1210),
    ("MER0013", "Nilgiri Daily Needs", 5411, "grocery", "Kotak Mahindra Bank", 410),
    ("MER0014", "Konark Fuel Station", 5541, "fuel", "ICICI Bank", 1700),
    ("MER0015", "Malabar Petro Services", 5542, "fuel", "HDFC Bank", 890),
    ("MER0016", "Arogya Plus Chemists", 5912, "pharmacy", "State Bank of India", 760),
    ("MER0017", "Swasthya Medical Hall", 5912, "pharmacy", "Kotak Mahindra Bank", 300),
    ("MER0018", "Tarang Digital Store", 5732, "electronics", "ICICI Bank", 1050),
    ("MER0019", "Chakra Appliances", 5732, "electronics", "Axis Bank", 1500),
    ("MER0020", "Safar Rail Bookings", 4112, "travel", "Axis Bank", 1390),
    ("MER0021", "Udaan Air Tickets", 4722, "travel", "Kotak Mahindra Bank", 690),
    ("MER0022", "Raahi Bus Lines", 4131, "travel", "Bank of Baroda", 1120),
    ("MER0023", "Jalnidhi Water Utility", 4900, "utilities", "HDFC Bank", 2400),
    ("MER0024", "Sampark Telecom Recharge", 4814, "utilities", "Axis Bank", 980),
    ("MER0025", "Darpan DTH Services", 4899, "utilities", "ICICI Bank", 1280),
]
ACQ_HANDLE = {
    "HDFC Bank": "hdfcbank", "ICICI Bank": "icici", "Axis Bank": "axisbank",
    "State Bank of India": "sbi", "Kotak Mahindra Bank": "kotak", "Bank of Baroda": "barodampay",
}
ACQ_CODE = {  # invented acquirer tags for ARNs; deliberately not BIN-shaped
    "HDFC Bank": "AQ01", "ICICI Bank": "AQ02", "Axis Bank": "AQ03",
    "State Bank of India": "AQ04", "Kotak Mahindra Bank": "AQ05", "Bank of Baroda": "AQ06",
}
ISSUER_BANKS = [
    "HDFC Bank", "State Bank of India", "ICICI Bank", "Axis Bank",
    "Kotak Mahindra Bank", "Punjab National Bank", "Bank of Baroda", "Union Bank of India",
]
PSP_HANDLES = ["okhdfc", "oksbi", "okicici", "okaxis", "ybl", "ibl", "axl", "paytm"]
P2P_BANKS = ISSUER_BANKS

FIRST_NAMES = [
    "Aarav", "Vihaan", "Aditya", "Arjun", "Sai", "Reyansh", "Kabir", "Ishaan", "Rohan", "Siddharth",
    "Ananya", "Diya", "Saanvi", "Aadhya", "Kavya", "Meera", "Pooja", "Sneha", "Tanvi", "Nandini",
    "Farhan", "Zoya", "Gurpreet", "Harleen", "Joseph", "Anjana", "Venkat", "Lavanya", "Tenzin", "Bhavna",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Nair", "Reddy", "Patel", "Chatterjee", "Banerjee", "Kulkarni", "Joshi", "Rao",
    "Pillai", "Mehta", "Gill", "Bhat", "Das", "Mishra", "Saxena", "Khan", "Fernandes", "Shetty",
    "Naidu", "Ghosh", "Agarwal", "Sethi", "Bose",
]
MOBILE_PREFIXES = ["98", "97", "96", "99", "94", "93", "90", "89", "88", "87", "78", "70", "63"]

# Fixed people the demo depends on.
DEMO_ISSUER = "HDFC Bank"
FIXED_CARDHOLDERS = {
    # id: (name, mobile_masked, vpa_local, handle, credit_last4)
    "CH0001": ("Ritika Deshpande", "98****4312", "ritika.d", "okhdfc", "7314"),
    "CH0017": ("Karan Malhotra", "97****8820", "karanm", "okhdfc", "2286"),
}
STAFF = [
    ("OPS001", "Neha Kulkarni", "99****1047", "issuer_ops"),
    ("OPS002", "Imran Qureshi", "98****6631", "issuer_ops"),
    ("SUP001", "Lakshmi Iyer", "96****5508", "risk_supervisor"),
]

# Illustrative UPI response codes, NOT verified against the NPCI code list.
RESP_FAILED = ["U30", "Z9", "ZM", "U16", "XH"]
RESP_PENDING = ["RB"]
RESP_REVERSED = ["RR"]

FUNDING_WEIGHTS = {"RUPAY_CREDIT_CARD": 45, "RUPAY_DEBIT_CARD": 15, "BANK_ACCOUNT": 30, "CREDIT_LINE": 10}
STATUS_WEIGHTS = {"SUCCESS": 80, "FAILED": 12, "PENDING": 5, "REVERSED": 3}
P2M_MODE_WEIGHTS = {"UPI_QR": 55, "UPI_INTENT": 25, "UPI_COLLECT": 5, "ECOM": 15}
ECOM_CATEGORIES = {"electronics", "travel", "utilities"}
AMOUNT_RANGE = {  # whole rupees, per merchant category
    "grocery": (80, 4000), "fuel": (200, 5000), "pharmacy": (50, 3000),
    "electronics": (500, 60000), "travel": (300, 25000), "utilities": (100, 6000),
}
P2P_RANGE = (50, 10000)

# MDR model. Plausible, not regulatory: the demo only needs the contrast
# "credit card P2M above 2000 pays MDR, bank-account UPI pays zero".
MDR_RATE = {
    "grocery": Decimal("0.0110"), "fuel": Decimal("0.0050"), "pharmacy": Decimal("0.0110"),
    "electronics": Decimal("0.0150"), "travel": Decimal("0.0150"), "utilities": Decimal("0.0070"),
}
CC_ZERO_MDR_UPTO = Decimal("2000")
MDR_FUNDINGS = {"RUPAY_CREDIT_CARD", "CREDIT_LINE"}

# ---------------------------------------------------------------------------
# Reason codes and rules
# ---------------------------------------------------------------------------
VERIFY_CIRCULAR = "VERIFY - read from RuPay Consolidated Circular"
# Descriptions are placeholders too; replace them along with source_circular.
REASON_CODES = [
    ("1001", "retrieval_request", "Cardholder does not recognise the transaction", "issuer"),
    ("1002", "retrieval_request", "Cardholder requests copy of transaction record", "issuer"),
    ("1003", "retrieval_request", "Copy required for fraud analysis", "issuer"),
    ("1004", "retrieval_request", "Copy required for legal process", "issuer"),
    ("1061", "goods_and_services", "Credit not processed", "issuer"),
    ("1062", "goods_and_services", "Goods or services not as described or defective", "issuer"),
    ("1063", "goods_and_services", "Cancelled transaction or recurring payment", "issuer"),
    ("1064", "goods_and_services", "Goods or services not received", "issuer"),
    ("1065", "goods_and_services", "Customer account debited but merchant not credited", "issuer"),
    ("1081", "processing_error", "Late presentment", "issuer"),
    ("1082", "processing_error", "Incorrect transaction amount", "issuer"),
    ("1083", "processing_error", "Paid by other means", "issuer"),
    ("1084", "processing_error", "Duplicate processing", "issuer"),
    ("1102", "fulfilment", "Retrieval request fulfilled", "acquirer"),
    ("1103", "fulfilment", "Retrieval request not fulfilled", "acquirer"),
]

# IMPORTANT FOR THE APPLICATION: every number below is PROVISIONAL. While a
# row's source_note is "VERIFY" (and source_circular / source_page are empty),
# the application MUST NOT present any figure from that row as circular-backed.
# It may run the calculator against it, but the answer has to say the figure is
# provisional and unverified, and must not cite a circular for it. Replace these
# values with figures read from the corpus and fill in the source columns.
CHARGEBACK_RULES = [
    # claim_type, code, cb_window, repr_window, evidence, penalty/day, penalty_after,
    # eligible_funding_sources, required_txn_type, required_status,
    # required_credited_to_merchant (true|false|any), route_to_human
    ("not_credited_to_merchant", "1065", 120, 30,
     "transaction_receipt|bank_statement_showing_debit|merchant_non_credit_confirmation", 100, 5,
     "RUPAY_CREDIT_CARD", "P2M", "SUCCESS", "false", False),
    ("goods_not_received", "1064", 120, 30,
     "order_confirmation|expected_delivery_date|proof_of_merchant_contact", 0, None,
     "RUPAY_CREDIT_CARD", "P2M", "SUCCESS", "true", False),
    ("duplicate_processing", "1084", 120, 30,
     "both_transaction_rrns|statement_showing_two_debits", 0, None,
     "RUPAY_CREDIT_CARD", "P2M", "SUCCESS", "any", False),
    ("credit_not_processed", "1061", 120, 30,
     "refund_or_cancellation_acknowledgement|statement_showing_no_credit", 0, None,
     "RUPAY_CREDIT_CARD", "P2M", "SUCCESS", "true", False),
    # Fraud claims are never decided by the agent: route_to_human=true sends them to risk_supervisor.
    ("not_recognised", "1001", 120, 30,
     "cardholder_declaration|card_status_at_txn_time|device_and_login_details", 0, None,
     "RUPAY_CREDIT_CARD", "any", "SUCCESS", "any", True),
]

# Illustrative UPI response codes. Descriptions are NOT verified against the NPCI
# code list: source_note=VERIFY, so the app must label explanations as provisional.
RESPONSE_CODES = [
    ("U30", "UPI", "FAILED", "Debit has failed",
     "The remitter bank could not debit the account (technical or account-level decline)",
     "No money left the account. Retry, or contact the bank if the account was debited."),
    ("Z9", "UPI", "FAILED", "Insufficient funds in remitter account",
     "The available balance was lower than the transaction amount",
     "No money left the account. Retry after funding the account."),
    ("ZM", "UPI", "FAILED", "Invalid UPI PIN",
     "The UPI PIN entered did not match", "No money left the account. Retry with the correct UPI PIN."),
    ("U16", "UPI", "FAILED", "Risk threshold exceeded",
     "The transaction was declined by risk controls", "Contact the issuing bank before retrying."),
    ("XH", "UPI", "FAILED", "Remitter account does not exist",
     "The linked account could not be found at the remitter bank", "Re-link the account in the UPI app."),
    ("RB", "UPI", "PENDING", "Deemed transaction - final status awaited",
     "The request timed out and the final status is still being reconciled",
     "Wait for automatic reconciliation before raising a complaint."),
    ("RR", "UPI", "REVERSED", "Transaction reversed",
     "The debit was reversed back to the remitter", "Check the account statement for the reversal credit."),
]

# ---------------------------------------------------------------------------
# The ten planted demo rows
# ---------------------------------------------------------------------------
PLANTED = [
    # suffix, funding, type, mode, amount, status, days_ago, cardholder, merchant,
    # credited, hh:mm:ss, response_code, purpose
    ("01", "RUPAY_CREDIT_CARD", "P2M", "UPI_QR", "4500", "SUCCESS", 12, "CH0001", "MER0004",
     False, (13, 42, 10), None, "Not credited, inside 1065 window: raise chargeback"),
    ("02", "RUPAY_CREDIT_CARD", "P2M", "UPI_QR", "4500", "SUCCESS", 141, "CH0001", "MER0004",
     False, (13, 42, 10), None, "Same as 001 but 141 days old: outside window, refuse"),
    ("03", "BANK_ACCOUNT", "P2M", "UPI_QR", "1800", "FAILED", 4, "CH0001", "MER0006",
     False, (9, 5, 44), "Z9", "Bank-account UPI failure: not a card chargeback"),
    ("04", "RUPAY_CREDIT_CARD", "P2P", "UPI_INTENT", "2500", "SUCCESS", 9, "CH0001", None,
     None, (20, 11, 3), None, "Credit card funded P2P: the rule exception"),
    ("05", "RUPAY_CREDIT_CARD", "P2M", "UPI_QR", "6200", "SUCCESS", 15, "CH0001", "MER0005",
     False, (18, 27, 51), None, "Case already RAISED: duplicate rejection"),
    ("06", "RUPAY_CREDIT_CARD", "P2M", "ECOM", "15000", "SUCCESS", 6, "CH0001", "MER0009",
     True, (2, 14, 37), None, "Fraud / not-recognised claim on ECOM"),
    ("07", "RUPAY_CREDIT_CARD", "P2M", "UPI_QR", "3200", "SUCCESS", 8, "CH0001", "MER0001",
     True, (11, 30, 0), None, "MDR contrast: credit card pays MDR"),
    ("08", "BANK_ACCOUNT", "P2M", "UPI_QR", "3200", "SUCCESS", 8, "CH0001", "MER0001",
     True, (11, 45, 0), None, "MDR contrast: bank account pays zero"),
    ("09", "RUPAY_CREDIT_CARD", "P2M", "UPI_QR", "2100", "SUCCESS", 10, "CH0017", "MER0002",
     False, (16, 8, 22), None, "Different cardholder (CH0017): RBAC denial"),
    ("10", "RUPAY_CREDIT_CARD", "P2M", "ECOM", "8900", "SUCCESS", 20, "CH0001", "MER0005",
     True, (21, 3, 9), None, "Goods not received (1064), merchant credited"),
]
PLANTED_RRN = {s: "1" + "0" * 9 + s for s, *_ in PLANTED}  # 100000000001 .. 100000000010
P2P_PAYEE_FOR_004 = "su***@oksbi"

# (case_id, rrn, claim_type, status, raised_by, created_days_ago, withdrawn_by, withdrawn_days_ago, withdrawn_reason)
SEED_CASES = [
    ("CB-SEED-000001", "100000000005", "not_credited_to_merchant", "RAISED", "OPS001", 3, None, None, None),
    ("CB-SEED-000002", "100000000007", "duplicate_processing", "WITHDRAWN", "OPS002", 5, "SUP001", 2,
     "Statement review showed a single debit; claim not substantiated"),
]

PAN_LIKE = re.compile(r"\d{13,}")  # any run of 13+ digits is treated as a possible PAN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def money(x: Decimal | int | str) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def mask_vpa(local: str, handle: str) -> str:
    return f"{local[:2].lower()}***@{handle}"


def at(as_of: date, days_ago: int, hh: int, mm: int, ss: int) -> datetime:
    d = as_of - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST)


def yddd(dt: datetime) -> str:
    return f"{dt.year % 10}{dt.timetuple().tm_yday:03d}"


def compute_mdr(funding: str, txn_type: str, status: str, amount: Decimal, category: str | None) -> Decimal:
    # MDR is only charged on settled P2M card/credit-line spend above the small-ticket limit.
    if txn_type != "P2M" or status != "SUCCESS" or funding not in MDR_FUNDINGS:
        return money(0)
    if amount <= CC_ZERO_MDR_UPTO:
        return money(0)
    return money(amount * MDR_RATE[category])


def allocate(n: int, weights: dict[str, int]) -> list[str]:
    """Exact largest-remainder split, so the distribution is stable rather than sampled."""
    total = sum(weights.values())
    raw = {k: n * w / total for k, w in weights.items()}
    counts = {k: int(v) for k, v in raw.items()}
    short = n - sum(counts.values())
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[:short]:
        counts[k] += 1
    return [k for k in weights for _ in range(counts[k])]


def weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def build_merchants(as_of: date) -> list[dict]:
    return [
        {"merchant_id": mid, "merchant_name": name, "mcc": mcc, "category": cat,
         "acquirer_bank": acq, "onboarded_date": (as_of - timedelta(days=age)).isoformat()}
        for mid, name, mcc, cat, acq, age in MERCHANTS[:N_MERCHANTS]
    ]


def build_cardholders(rng: random.Random) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    credit_last4: dict[str, str] = {}
    used_names = {v[0] for v in FIXED_CARDHOLDERS.values()} | {s[1] for s in STAFF}
    for i in range(1, N_CARDHOLDERS + 1):
        cid = f"CH{i:04d}"
        if cid in FIXED_CARDHOLDERS:
            name, mobile, local, handle, last4 = FIXED_CARDHOLDERS[cid]
            bank = DEMO_ISSUER
        else:
            while True:
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                if name not in used_names:
                    break
            mobile = f"{rng.choice(MOBILE_PREFIXES)}****{rng.randint(0, 9999):04d}"
            local = name.split()[0].lower() + str(rng.randint(1, 99))
            handle = rng.choice(PSP_HANDLES)
            last4 = f"{rng.randint(0, 9999):04d}"
            bank = rng.choice(ISSUER_BANKS)
        used_names.add(name)
        credit_last4[cid] = last4
        rows.append({"cardholder_id": cid, "name": name, "mobile_masked": mobile,
                     "registered_vpa": mask_vpa(local, handle), "role": "cardholder", "issuer_bank": bank})
    for sid, name, mobile, role in STAFF:
        rows.append({"cardholder_id": sid, "name": name, "mobile_masked": mobile,
                     "registered_vpa": None, "role": role, "issuer_bank": DEMO_ISSUER})
    return rows, credit_last4


def build_filler_txns(rng: random.Random, as_of: date, cardholders: list[dict],
                      credit_last4: dict[str, str], merchants: list[dict]) -> list[dict]:
    # CH0001 is excluded so the logged-in demo user sees exactly the planted rows.
    pool = [c for c in cardholders if c["role"] == "cardholder" and c["cardholder_id"] != "CH0001"]
    debit_last4 = {c["cardholder_id"]: f"{rng.randint(0, 9999):04d}" for c in pool}
    ecom_merchants = [m for m in merchants if m["category"] in ECOM_CATEGORIES]

    fundings = allocate(N_FILLER_TXN, FUNDING_WEIGHTS)
    statuses = allocate(N_FILLER_TXN, STATUS_WEIGHTS)
    rng.shuffle(fundings)
    rng.shuffle(statuses)

    used_rrn: set[str] = set()
    rows: list[dict] = []
    for funding, status in zip(fundings, statuses):
        ch = rng.choice(pool)
        days_ago = rng.randint(1, MAX_DAYS_AGO)
        ts = at(as_of, days_ago, rng.randint(7, 22), rng.randint(0, 59), rng.randint(0, 59))

        while True:
            rrn = f"{yddd(ts)}{ts.hour:02d}{rng.randint(0, 999999):06d}"
            if rrn not in used_rrn:
                used_rrn.add(rrn)
                break

        # Only bank-account UPI may be P2P in filler; card-funded P2P is the planted exception.
        txn_type = "P2P" if funding == "BANK_ACCOUNT" and rng.random() < 0.35 else "P2M"
        if txn_type == "P2M":
            mode = weighted(rng, P2M_MODE_WEIGHTS)
            m = rng.choice(ecom_merchants if mode == "ECOM" else merchants)
            lo, hi = AMOUNT_RANGE[m["category"]]
            payee_vpa = mask_vpa(m["merchant_name"].replace(" ", ""), ACQ_HANDLE[m["acquirer_bank"]])
            merchant_id, mcc, category, acquirer = m["merchant_id"], m["mcc"], m["category"], m["acquirer_bank"]
        else:
            mode = rng.choice(["UPI_INTENT", "UPI_COLLECT"])
            lo, hi = P2P_RANGE
            payee_vpa = mask_vpa(rng.choice(FIRST_NAMES), rng.choice(PSP_HANDLES))
            merchant_id = mcc = category = None
            acquirer = rng.choice(P2P_BANKS)  # beneficiary bank for P2P

        amount = money(rng.randint(lo, hi))
        if rng.random() < 0.3:
            amount = money(amount + Decimal(rng.randint(1, 99)) / 100)

        is_card = funding in ("RUPAY_CREDIT_CARD", "RUPAY_DEBIT_CARD")
        presented = is_card and status in ("SUCCESS", "REVERSED")
        card_last4 = None
        if funding == "RUPAY_CREDIT_CARD":
            card_last4 = credit_last4[ch["cardholder_id"]]
        elif funding == "RUPAY_DEBIT_CARD":
            card_last4 = debit_last4[ch["cardholder_id"]]

        response_code = {
            "SUCCESS": None, "FAILED": rng.choice(RESP_FAILED),
            "PENDING": rng.choice(RESP_PENDING), "REVERSED": rng.choice(RESP_REVERSED),
        }[status]

        refund_status, refund_ts = "NONE", None
        if txn_type == "P2M" and status == "SUCCESS" and days_ago >= 3:
            r = rng.random()
            if r < 0.08:
                refund_status = "INITIATED" if r < 0.04 else "COMPLETED"
                refund_ts = ts + timedelta(days=rng.randint(1, min(10, days_ago - 2)), hours=rng.randint(0, 5))

        rows.append({
            "rrn": rrn,
            "arn": f"ARN-{ACQ_CODE.get(acquirer, 'AQ09')}-{yddd(ts)}-{rng.randint(0, 9999999):07d}" if presented else None,
            "auth_code": "".join(rng.choices(string.ascii_uppercase + string.digits, k=6)) if presented else None,
            "funding_source": funding,
            "card_last4": card_last4,
            "txn_mode": mode,
            "txn_type": txn_type,
            "payer_vpa": ch["registered_vpa"],
            "payee_vpa": payee_vpa,
            "merchant_id": merchant_id,
            "mcc": mcc,
            "amount": amount,
            "mdr_charged": compute_mdr(funding, txn_type, status, amount, category),
            "txn_timestamp": ts.isoformat(),
            "settlement_date": (ts.date() + timedelta(days=1)).isoformat() if status == "SUCCESS" else None,
            "status": status,
            "response_code": response_code,
            "credited_to_merchant": (status == "SUCCESS") if txn_type == "P2M" else None,
            "refund_status": refund_status,
            "refund_timestamp": refund_ts.isoformat() if refund_ts else None,
            "cardholder_id": ch["cardholder_id"],
            "issuer_bank": ch["issuer_bank"],
            "acquirer_bank": acquirer,
        })
    return rows


def build_planted_txns(as_of: date, cardholders: list[dict], credit_last4: dict[str, str],
                       merchants: list[dict]) -> list[dict]:
    # Planted rows draw nothing from the RNG, so they are identical whatever the filler counts are.
    ch_by_id = {c["cardholder_id"]: c for c in cardholders}
    m_by_id = {m["merchant_id"]: m for m in merchants}
    rows = []
    for (sfx, funding, txn_type, mode, amt, status, days_ago, cid, mid,
         credited, (hh, mm, ss), resp, _purpose) in PLANTED:
        ch = ch_by_id[cid]
        m = m_by_id.get(mid) if mid else None
        ts = at(as_of, days_ago, hh, mm, ss)
        amount = money(amt)
        is_card = funding.startswith("RUPAY_")
        presented = is_card and status in ("SUCCESS", "REVERSED")
        acquirer = m["acquirer_bank"] if m else "State Bank of India"
        rows.append({
            "rrn": PLANTED_RRN[sfx],
            "arn": f"ARN-{ACQ_CODE[acquirer]}-{yddd(ts)}-{int(sfx):07d}" if presented else None,
            "auth_code": f"DM{int(sfx):04d}" if presented else None,
            "funding_source": funding,
            "card_last4": credit_last4[cid] if funding == "RUPAY_CREDIT_CARD" else None,
            "txn_mode": mode,
            "txn_type": txn_type,
            "payer_vpa": ch["registered_vpa"],
            "payee_vpa": mask_vpa(m["merchant_name"].replace(" ", ""), ACQ_HANDLE[acquirer]) if m else P2P_PAYEE_FOR_004,
            "merchant_id": mid,
            "mcc": m["mcc"] if m else None,
            "amount": amount,
            "mdr_charged": compute_mdr(funding, txn_type, status, amount, m["category"] if m else None),
            "txn_timestamp": ts.isoformat(),
            "settlement_date": (ts.date() + timedelta(days=1)).isoformat() if status == "SUCCESS" else None,
            "status": status,
            "response_code": resp,
            "credited_to_merchant": credited,
            "refund_status": "NONE",
            "refund_timestamp": None,
            "cardholder_id": cid,
            "issuer_bank": ch["issuer_bank"],
            "acquirer_bank": acquirer,
        })
    return rows


def build_reason_codes() -> list[dict]:
    return [{"code": c, "category": cat, "description": d, "raised_by": by, "source_circular": VERIFY_CIRCULAR}
            for c, cat, d, by in REASON_CODES]


def build_rules() -> list[dict]:
    return [{"claim_type": ct, "reason_code": code, "chargeback_window_days": cbw,
             "representment_window_days": rw, "required_evidence": ev, "penalty_per_day": pen,
             "penalty_starts_after_days": after, "eligible_funding_sources": funds,
             "required_txn_type": ttype, "required_status": status,
             "required_credited_to_merchant": credited, "route_to_human": human,
             "source_circular": "", "source_page": "", "source_note": "VERIFY"}
            for ct, code, cbw, rw, ev, pen, after, funds, ttype, status, credited, human in CHARGEBACK_RULES]


def build_response_codes() -> list[dict]:
    return [{"code": c, "rail": rail, "status_class": sc, "description": d, "likely_cause": cause,
             "customer_guidance": g, "source_circular": "", "source_note": "VERIFY"}
            for c, rail, sc, d, cause, g in RESPONSE_CODES]


def sql_lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, Decimal):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def build_seed_sql(as_of: date, txns: list[dict], rules: list[dict]) -> str:
    by_rrn = {t["rrn"]: t for t in txns}
    rule_by_claim = {r["claim_type"]: r for r in rules}
    role_of = {s[0]: s[3] for s in STAFF}
    cols = ("case_id, rrn, cardholder_id, reason_code, claim_type, amount, status, raised_by, raised_by_role, "
            "idempotency_key, representment_due_at, withdrawn_by, withdrawn_reason, withdrawn_at, "
            "created_at, updated_at")
    rows = []
    for case_id, rrn, claim, status, raised_by, c_ago, w_by, w_ago, w_reason in SEED_CASES:
        txn, rule = by_rrn[rrn], rule_by_claim[claim]
        created = at(as_of, c_ago, 10, 30, 0)
        withdrawn = at(as_of, w_ago, 15, 0, 0) if w_ago is not None else None
        due = created + timedelta(days=int(rule["representment_window_days"]))
        vals = [case_id, rrn, txn["cardholder_id"], rule["reason_code"], claim, txn["amount"], status,
                raised_by, role_of[raised_by], f"seed:{rrn}:{claim}", due.isoformat(), w_by, w_reason,
                withdrawn.isoformat() if withdrawn else None, created.isoformat(),
                (withdrawn or created).isoformat()]
        rows.append("  (" + ", ".join(sql_lit(v) for v in vals) + ")")
    return (
        f"-- Generated by tools/make_data.py --as-of {as_of.isoformat()}. Do not edit by hand.\n"
        "-- CB-SEED-000001: RAISED on 100000000005, so the duplicate-rejection path has something to collide with.\n"
        "-- CB-SEED-000002: WITHDRAWN by SUP001 on 100000000007, so the withdrawn-case block can be demonstrated.\n"
        "-- representment_due_at uses a PROVISIONAL (source_note=VERIFY) window.\n"
        f"INSERT INTO chargeback_cases ({cols})\nVALUES\n" + ",\n".join(rows) + "\n"
        "ON CONFLICT (idempotency_key) DO NOTHING;\n"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def days_elapsed(as_of: date, txn: dict) -> int:
    return (as_of - datetime.fromisoformat(txn["txn_timestamp"]).date()).days


def validate(as_of: date, tables: dict[str, list[dict]], extra_text: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    txns = tables["transactions"]
    by_rrn = {t["rrn"]: t for t in txns}

    # 1
    rrns = [t["rrn"] for t in txns]
    if len(rrns) != len(set(rrns)):
        errors.append("1: duplicate RRNs present")
    bad = [r for r in rrns if not re.fullmatch(r"\d{12}", r)]
    if bad:
        errors.append(f"1: RRNs not exactly 12 digits: {bad[:5]}")

    # 2
    for sfx, funding, txn_type, *_ in PLANTED:
        t = by_rrn.get(PLANTED_RRN[sfx])
        if t is None:
            errors.append(f"2: planted RRN {PLANTED_RRN[sfx]} missing")
        elif (t["funding_source"], t["txn_type"]) != (funding, txn_type):
            errors.append(f"2: {PLANTED_RRN[sfx]} is {t['funding_source']}/{t['txn_type']}, expected {funding}/{txn_type}")

    # 3
    cc_p2p = [t["rrn"] for t in txns if t["funding_source"] == "RUPAY_CREDIT_CARD" and t["txn_type"] == "P2P"]
    if cc_p2p != ["100000000004"]:
        errors.append(f"3: credit-card-funded P2P rows must be exactly [100000000004], got {cc_p2p}")

    bank = [t for t in txns if t["funding_source"] == "BANK_ACCOUNT"]
    # 4
    bad = [t["rrn"] for t in bank if t["mdr_charged"] != 0]
    if bad:
        errors.append(f"4: BANK_ACCOUNT rows with non-zero mdr_charged: {bad}")
    # 5
    bad = [t["rrn"] for t in bank if t["arn"] or t["auth_code"] or t["card_last4"]]
    if bad:
        errors.append(f"5: BANK_ACCOUNT rows carrying arn/auth_code/card_last4: {bad}")

    # 6
    ch_ids = {c["cardholder_id"] for c in tables["cardholders"]}
    bad = sorted({t["cardholder_id"] for t in txns} - ch_ids)
    if bad:
        errors.append(f"6: cardholder_ids missing from cardholders: {bad}")
    for cid in ("CH0001", "CH0017"):
        c = next((c for c in tables["cardholders"] if c["cardholder_id"] == cid), None)
        if c is None or c["role"] != "cardholder":
            errors.append(f"6: {cid} must exist with role=cardholder")

    # 7
    m_ids = {m["merchant_id"] for m in tables["merchants"]}
    bad = sorted({t["merchant_id"] for t in txns if t["merchant_id"]} - m_ids)
    if bad:
        errors.append(f"7: merchant_ids missing from merchants: {bad}")

    # 8
    rule = next((r for r in tables["chargeback_rules"] if r["reason_code"] == "1065"), None)
    if rule is None:
        errors.append("8: no chargeback rule for 1065")
    else:
        window = int(rule["chargeback_window_days"])
        d1, d2 = days_elapsed(as_of, by_rrn["100000000001"]), days_elapsed(as_of, by_rrn["100000000002"])
        notes.append(f"window(1065)={window}d  |  100000000001 elapsed={d1}d (inside)  |  "
                     f"100000000002 elapsed={d2}d (outside)")
        if d1 > window:
            errors.append(f"8: 100000000001 elapsed {d1}d exceeds 1065 window {window}d")
        if d2 <= window:
            errors.append(f"8: 100000000002 elapsed {d2}d does not exceed 1065 window {window}d")

    # 9
    for name, rows in tables.items():
        for i, row in enumerate(rows):
            for col, v in row.items():
                if PAN_LIKE.search(fmt(v)):
                    errors.append(f"9: {name} row {i} column {col} has a 13+ digit run")
    for name, text in extra_text.items():
        if PAN_LIKE.search(text):
            errors.append(f"9: {name} contains a 13+ digit run")

    # Supporting invariants the demo relies on.
    known_codes = {r["code"] for r in tables["response_codes"]}
    bad = sorted({t["response_code"] for t in txns if t["response_code"]} - known_codes)
    if bad:
        errors.append(f"extra: response codes missing from response_codes.csv: {bad}")
    for case_id, rrn, *_ in SEED_CASES:
        if rrn not in by_rrn:
            errors.append(f"extra: seed case {case_id} references unknown RRN {rrn}")
    bad = [t["rrn"] for t in txns if t["funding_source"] == "RUPAY_CREDIT_CARD" and t["txn_type"] == "P2M"
           and t["status"] == "SUCCESS" and t["amount"] > CC_ZERO_MDR_UPTO and t["mdr_charged"] <= 0]
    if bad:
        errors.append(f"extra: settled credit-card P2M above {CC_ZERO_MDR_UPTO} with zero MDR: {bad}")
    bad = [t["rrn"] for t in txns if not 1 <= days_elapsed(as_of, t) <= MAX_DAYS_AGO]
    if bad:
        errors.append(f"extra: txn_timestamp outside 1..{MAX_DAYS_AGO} days before as_of: {bad}")
    bad = [t["rrn"] for t in txns if t["refund_timestamp"]
           and datetime.fromisoformat(t["refund_timestamp"]).date() >= as_of]
    if bad:
        errors.append(f"extra: refund_timestamp on or after as_of: {bad}")
    return errors, notes


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r[c]) for c in cols])


def manifest(as_of: date, txns: list[dict], notes: list[str]) -> str:
    by_rrn = {t["rrn"]: t for t in txns}
    head = ["RRN", "Cardholder", "Funding", "Type", "Mode", "Amount", "MDR", "Status",
            "Credited", "Days ago", "Txn date", "Demo purpose"]
    body = []
    for sfx, *_rest in PLANTED:
        t = by_rrn[PLANTED_RRN[sfx]]
        body.append([t["rrn"], t["cardholder_id"], t["funding_source"], t["txn_type"], t["txn_mode"],
                     fmt(t["amount"]), fmt(t["mdr_charged"]), t["status"], fmt(t["credited_to_merchant"]) or "-",
                     str(days_elapsed(as_of, t)), t["txn_timestamp"][:10], _rest[-1]])
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(len(head))]
    line = lambda r: "| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return (
        f"# Demo rows (as_of {as_of.isoformat()})\n\n"
        + "\n".join([line(head), sep, *map(line, body)])
        + "\n\nSeed cases (data/seed_cases.sql):\n"
        + "".join(f"- {c[0]} {c[3]} against {c[1]} ({c[2]}), raised by {c[4]}"
                  + (f", withdrawn by {c[6]}" if c[6] else "") + "\n" for c in SEED_CASES)
        + "".join(f"\nCheck 8: {n}\n" for n in notes)
        + "\nAll chargeback_rules rows have source_note=VERIFY: figures are provisional, not circular-backed.\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", type=Path, default=Path("./data"))
    args = ap.parse_args()
    as_of: date = args.as_of

    rng = random.Random(SEED)
    merchants = build_merchants(as_of)
    cardholders, credit_last4 = build_cardholders(rng)
    txns = build_filler_txns(rng, as_of, cardholders, credit_last4, merchants)
    txns += build_planted_txns(as_of, cardholders, credit_last4, merchants)
    reason_codes = build_reason_codes()
    rules = build_rules()
    seed_sql = build_seed_sql(as_of, txns, rules)

    tables = {"transactions": txns, "cardholders": cardholders, "merchants": merchants,
              "reason_codes": reason_codes, "chargeback_rules": rules,
              "response_codes": build_response_codes()}
    errors, notes = validate(as_of, tables, {"seed_cases.sql": seed_sql})
    if errors:
        print("VALIDATION FAILED - nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "transactions.csv", TXN_COLS, txns)
    write_csv(out / "cardholders.csv", CARDHOLDER_COLS, cardholders)
    write_csv(out / "merchants.csv", MERCHANT_COLS, merchants)
    write_csv(out / "reason_codes.csv", REASON_COLS, reason_codes)
    write_csv(out / "chargeback_rules.csv", RULE_COLS, rules)
    write_csv(out / "response_codes.csv", RESPONSE_COLS, tables["response_codes"])
    (out / "seed_cases.sql").write_text(seed_sql, encoding="utf-8", newline="\n")
    md = manifest(as_of, txns, notes)
    (out / "MANIFEST.md").write_text(md, encoding="utf-8", newline="\n")

    print(f"as_of={as_of.isoformat()}  out={out}")
    for name, rows in tables.items():
        print(f"  {name}.csv: {len(rows)} rows")
    print("  validation: all checks passed\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
