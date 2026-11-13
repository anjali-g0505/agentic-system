"""Access control at the tool boundary. Identity comes from the authenticated session (RunnableConfig), never
from model-supplied arguments: the model cannot choose whose data it reads or which role it acts as."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig

from app import audit
from app.masking import to_tool_output

CARDHOLDER, ISSUER_OPS, RISK_SUPERVISOR = "cardholder", "issuer_ops", "risk_supervisor"
ALL_ROLES = (CARDHOLDER, ISSUER_OPS, RISK_SUPERVISOR)


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str


class AccessDenied(Exception):
    pass


def principal_from(config: RunnableConfig | None) -> Principal:
    p = ((config or {}).get("configurable") or {}).get("principal")
    if not isinstance(p, Principal):
        raise AccessDenied("No authenticated principal on this request")
    return p


def requires_roles(*roles: str) -> Callable:
    """Wrap a tool implementation: enforce role, audit every call (allowed or denied), mask the output."""
    def deco(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[str]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, config: RunnableConfig, **kwargs: Any) -> str:
            confirmed_by = ((config or {}).get("configurable") or {}).get("confirmed_by")
            try:
                p = principal_from(config)
            except AccessDenied as e:
                return to_tool_output({"status": "DENIED", "message": str(e)})
            if p.role not in roles:
                result = {"status": "DENIED",
                          "message": f"Role '{p.role}' is not permitted to use {fn.__name__.lstrip("_")}. "
                                     f"Allowed roles: {', '.join(roles)}."}
                await audit.record(p, fn.__name__.lstrip("_"), kwargs, "DENIED", result["message"], confirmed_by)
                return to_tool_output(result)
            try:
                result = await fn(p, *args, confirmed_by=confirmed_by, **kwargs)
            except Exception as e:
                await audit.record(p, fn.__name__.lstrip("_"), kwargs, "ERROR", f"{type(e).__name__}: {e}", confirmed_by)
                return to_tool_output({"status": "ERROR", "message": "The tool failed; nothing was changed."})
            await audit.record(p, fn.__name__.lstrip("_"), kwargs, result.get("status", "OK"),
                               str(result.get("summary") or result.get("message") or result.get("status")),
                               confirmed_by)
            return to_tool_output(result)
        return wrapper
    return deco
