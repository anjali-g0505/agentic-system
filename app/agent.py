"""Single LangGraph agent built by hand: agent -> (tools | approval) -> agent ... -> finalize.

Write tools are NOT registered in the ToolNode. The only path to a write is the approval node, which calls
interrupt() and executes the write only if a human resumes with "approve". Durable via PostgresSaver.
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy, interrupt

from app.llm import generator
from app.masking import scrub
from app.rbac import principal_from
from app.tools import ALL_TOOLS, NO_REFUND_PROMISE, READ_TOOLS, WRITE_TOOLS, preview

SYSTEM = """You are the chargeback initiation desk assistant for RuPay credit cards used on UPI, working inside an
issuing bank. The signed-in user is {user_id} with role {role}. Roles: cardholder (customer), issuer_ops,
risk_supervisor. Access control is enforced by the tools; if a tool returns DENIED or NOT_FOUND, say so plainly.

How to work:
1. Operating-rule questions: call search_circulars (pass the rrn if the question is about a transaction, so the
   search is limited to that transaction's rail). Answer ONLY from the returned passages and cite each statement
   with its number in square brackets, e.g. [1] or [2] (the "ref" field). If the status is NOT_COVERED or NOT_VERIFIED, say the available circulars do not cover it. Never
   answer rules from general knowledge.
2. Transaction status: call get_transaction_status. State the rail. For failures, explain the response code using
   only the returned description and cause, and say that code meanings are provisional.
3. Eligibility: always call check_chargeback_eligibility; never judge eligibility yourself. Report the decision
   and each failed check exactly as returned. Never calculate dates, days or amounts yourself: only repeat figures
   the tool returned. If rule_source.provisional is true, say the figures are provisional and not yet verified
   against a circular.
4. Only issuer_ops can raise a chargeback (raise_chargeback). A cardholder can view transactions and lodge a
   dispute with the issuing bank (lodge_dispute) but cannot raise a chargeback. A previously withdrawn case blocks
   a new chargeback, but a dispute can still be lodged. Only risk_supervisor can withdraw a case (withdraw_case).
5. Suspected fraud or a transaction the cardholder does not recognise: never resolve it. Call refer_to_risk (or,
   for a cardholder, lodge_dispute with claim_type not_recognised, which refers it automatically).
6. Actions with side effects (raise_chargeback, lodge_dispute, withdraw_case, refer_to_risk): just call the tool.
   The system shows the human a confirmation dialog; do not ask for confirmation in text. Propose one at a time.
7. You cannot initiate, reverse or authorise a payment, and you must never promise or imply a refund.
8. Never ask for or repeat a full card number; refer to cards only by their last four digits.
Map the user's complaint to a claim_type: not_credited_to_merchant (debited but merchant not paid),
goods_not_received, duplicate_processing, credit_not_processed (refund promised by merchant but not received),
not_recognised (fraud / unrecognised). Be concise and use short paragraphs or bullets."""

REFUND_PROMISE = re.compile(
    r"[^.\n]*\b(you will (?:get|receive|be (?:refunded|credited))|refund (?:is|has been|will be) "
    r"(?:guaranteed|approved|processed|issued)|we will refund|guaranteed refund|money will be (?:returned|credited))"
    r"[^.\n]*[.\n]?", re.I)

model = generator.bind_tools(ALL_TOOLS)
tool_node = ToolNode(READ_TOOLS)
READ_BY_NAME = {t.name: t for t in READ_TOOLS}


async def agent(state: MessagesState, config: RunnableConfig) -> dict:
    p = principal_from(config)
    reply = await model.ainvoke([SystemMessage(SYSTEM.format(user_id=p.user_id, role=p.role)), *state["messages"]])
    return {"messages": [reply]}


def route(state: MessagesState) -> str:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return "finalize"
    return "approval" if any(tc["name"] in WRITE_TOOLS for tc in last.tool_calls) else "tools"


async def approval(state: MessagesState, config: RunnableConfig) -> dict:
    p = principal_from(config)
    calls = state["messages"][-1].tool_calls
    write_call = next(tc for tc in calls if tc["name"] in WRITE_TOOLS)

    # Interrupt first: everything before interrupt() re-runs on resume, so nothing with a side effect goes here.
    pv = await preview(write_call["name"], write_call["args"], p)
    if pv["blocked"]:
        write_result = json.dumps({"status": "BLOCKED", "message": pv["reason"],
                                   "note": "Not shown to the user for confirmation because it cannot succeed."})
    else:
        decision = interrupt({"type": "confirmation", "tool": write_call["name"], "args": write_call["args"],
                              "preview": pv, "requested_by": p.user_id})
        if decision == "approve":
            # confirmed_by is the authenticated user who resumed the thread (threads are scoped per user).
            cfg = {**config, "configurable": {**config["configurable"], "confirmed_by": p.user_id}}
            write_result = await WRITE_TOOLS[write_call["name"]].ainvoke(write_call["args"], cfg)
        else:
            write_result = json.dumps({"status": "REJECTED_BY_USER",
                                       "message": "The user declined at the confirmation step. Nothing was done."})

    out = []
    for tc in calls:
        if tc["id"] == write_call["id"]:
            content = write_result
        elif tc["name"] in WRITE_TOOLS:
            content = json.dumps({"status": "DEFERRED", "message": "Only one action is confirmed at a time."})
        else:
            content = await READ_BY_NAME[tc["name"]].ainvoke(tc["args"], config)
        out.append(ToolMessage(content=content, tool_call_id=tc["id"], name=tc["name"]))
    return {"messages": out}


async def finalize(state: MessagesState) -> dict:
    """Deterministic output guard: no PAN-like numbers, no refund promises, whatever the model wrote."""
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        return {}
    text = last.content if isinstance(last.content, str) else str(last.content)
    safe = scrub(text)
    if REFUND_PROMISE.search(safe):
        safe = REFUND_PROMISE.sub("", safe).strip() + f"\n\n{NO_REFUND_PROMISE}"
    if safe == text:
        return {}
    return {"messages": [AIMessage(content=safe, id=last.id)]}


def build_graph(checkpointer) -> object:
    g = StateGraph(MessagesState)
    g.add_node("agent", agent, retry_policy=RetryPolicy(max_attempts=3))
    g.add_node("tools", tool_node)
    g.add_node("approval", approval)
    g.add_node("finalize", finalize)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", "approval": "approval", "finalize": "finalize"})
    g.add_edge("tools", "agent")
    g.add_edge("approval", "agent")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
