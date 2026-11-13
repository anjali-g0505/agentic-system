"""Rail of a transaction, derived deterministically from its funding source."""
from __future__ import annotations

RAILS = {
    "RUPAY_CREDIT_CARD": ("RUPAY_CC_ON_UPI", "RuPay credit card on UPI", ["RUPAY", "UPI"]),
    "RUPAY_DEBIT_CARD": ("RUPAY_DEBIT_ON_UPI", "RuPay debit card on UPI", ["RUPAY", "UPI"]),
    "BANK_ACCOUNT": ("UPI", "UPI (bank account)", ["UPI"]),
    "CREDIT_LINE": ("UPI_CREDIT_LINE", "UPI (pre-sanctioned credit line)", ["UPI"]),
}


def rail_for(funding_source: str) -> dict:
    code, label, products = RAILS.get(funding_source, ("UNKNOWN", "Unknown rail", ["RUPAY", "UPI"]))
    return {"rail": code, "rail_label": label, "circular_products": products}


def products_for_question(question: str) -> list[str]:
    q = question.lower()
    picked = [p for p, keys in (("RUPAY", ("rupay", "card", "chargeback", "issuer", "acquirer")),
                                ("UPI", ("upi", "vpa", "psp", "collect", "qr", "udir")))
              if any(k in q for k in keys)]
    return picked or ["RUPAY", "UPI"]
