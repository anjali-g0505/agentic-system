"""Masking at the tool boundary: the model never receives a value it could leak."""
from __future__ import annotations

import json
import re

PAN_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def mask_card(last4: str | None) -> str | None:
    return f"•••• {last4}" if last4 else None


def scrub(text: str) -> str:
    """Last line of defence: any 13-19 digit run is treated as a PAN and redacted."""
    return PAN_RUN.sub("[REDACTED-CARD]", text)


def to_tool_output(payload: dict) -> str:
    return scrub(json.dumps(payload, default=str, ensure_ascii=False))
