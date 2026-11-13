"""FastAPI entrypoint."""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from app.config import require_groq, settings

require_groq()

from app import data  # noqa: E402
from app.agent import build_graph  # noqa: E402
from app.auth import current_principal, issue_token  # noqa: E402
from app.db import close_pool, open_pool, pool  # noqa: E402
from app.rbac import Principal  # noqa: E402
from app.retriever import retriever  # noqa: E402
from app.tools import list_cases  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

checkpoint_pool = AsyncConnectionPool(conninfo=settings.DATABASE_URL, open=False, max_size=10,
                                      kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    await checkpoint_pool.open(wait=True, timeout=15)
    await retriever.load()
    # Checkpointer tables are created by the ingest job (saver.setup()); the API never creates schema.
    app.state.graph = build_graph(AsyncPostgresSaver(checkpoint_pool))
    log.info("ready: %d chunks loaded for retrieval", len(retriever.chunks))
    yield
    await checkpoint_pool.close()
    await close_pool()


app = FastAPI(title="RuPay Credit Card on UPI - Chargeback Desk", lifespan=lifespan)
# The browser normally calls same-origin /api via nginx; explicit origins only for local Vite dev.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    code = exc.detail.get("code") if isinstance(exc.detail, dict) else f"http_{exc.status_code}"
    msg = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": msg}})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": str(exc.errors())}})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Internal error"}})


# --------------------------------------------------------------------------- models
class LoginRequest(BaseModel):
    user_id: str = Field(min_length=3, max_length=12)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class ConfirmRequest(BaseModel):
    conversation_id: str
    decision: Literal["approve", "reject"]


class ChatResponse(BaseModel):
    conversation_id: str
    status: Literal["complete", "awaiting_confirmation"]
    answer: str | None = None
    citations: list[dict] = []
    tool_events: list[dict] = []
    confirmation: dict | None = None


# --------------------------------------------------------------------------- helpers
def run_config(p: Principal, conversation_id: str) -> dict:
    # thread_id is namespaced by user, so nobody can read or resume another user's conversation.
    return {"configurable": {"thread_id": f"{p.user_id}:{conversation_id}", "principal": p}, "recursion_limit": 25}


def summarise_turn(messages: list) -> tuple[str | None, list[dict], list[dict]]:
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    turn = messages[last_human + 1:]
    citations, events, seen = [], [], set()
    for m in turn:
        if not isinstance(m, ToolMessage):
            continue
        try:
            payload = json.loads(m.content)
        except (TypeError, ValueError):
            payload = {}
        events.append({"tool": m.name, "status": payload.get("status")})
        for c in payload.get("citations", []) or []:
            key = (c["source_filename"], c["page_number"], c.get("clause"))
            if key not in seen:
                seen.add(key)
                citations.append({"kind": "circular", **c})
        elig = payload.get("eligibility") or {}
        if elig.get("rule_source"):
            rs = elig["rule_source"]
            citations.append({"kind": "rule", "source_filename": rs["file"], "claim_type": rs["claim_type"],
                              "source_circular": rs["source_circular"], "provisional": rs["provisional"]})
        if payload.get("response_code"):
            citations.append({"kind": "data", "source_filename": "response_codes.csv",
                              "code": payload["response_code"]["code"], "provisional": True})
    answer = next((m.content for m in reversed(turn) if isinstance(m, AIMessage) and not m.tool_calls), None)
    return answer, citations, events


async def respond(result: dict, conversation_id: str) -> ChatResponse:
    answer, citations, events = summarise_turn(result.get("messages", []))
    if result.get("__interrupt__"):
        return ChatResponse(conversation_id=conversation_id, status="awaiting_confirmation", citations=citations,
                            tool_events=events, confirmation=result["__interrupt__"][0].value)
    return ChatResponse(conversation_id=conversation_id, status="complete", answer=answer, citations=citations,
                        tool_events=events)


# --------------------------------------------------------------------------- routes
@app.get("/health")
async def health() -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM chunks")
        (chunks,) = await cur.fetchone()
    return {"status": "ok", "db": "ok", "chunks": chunks, "retriever_chunks": len(retriever.chunks)}


@app.get("/auth/users")
async def demo_users() -> list[dict]:
    return await data.list_demo_users()


@app.post("/auth/login")
async def login(body: LoginRequest) -> dict:
    token, user = await issue_token(body.user_id)
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/me")
async def me(p: Principal = Depends(current_principal)) -> dict:
    return {"user_id": p.user_id, "role": p.role}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, p: Principal = Depends(current_principal)) -> ChatResponse:
    graph = request.app.state.graph
    conversation_id = body.conversation_id or uuid.uuid4().hex
    cfg = run_config(p, conversation_id)
    state = await graph.aget_state(cfg)
    if state.next:
        raise HTTPException(409, {"code": "awaiting_confirmation",
                                  "message": "Confirm or reject the pending action first."})
    result = await graph.ainvoke({"messages": [HumanMessage(body.message)]}, cfg)
    return await respond(result, conversation_id)


@app.post("/chat/confirm", response_model=ChatResponse)
async def confirm(body: ConfirmRequest, request: Request, p: Principal = Depends(current_principal)) -> ChatResponse:
    graph = request.app.state.graph
    cfg = run_config(p, body.conversation_id)
    state = await graph.aget_state(cfg)
    if not state.next:
        raise HTTPException(409, {"code": "nothing_pending", "message": "There is no action awaiting confirmation."})
    result = await graph.ainvoke(Command(resume=body.decision), cfg)
    return await respond(result, body.conversation_id)


@app.get("/cases")
async def cases(p: Principal = Depends(current_principal)) -> dict:
    return json.loads(await list_cases.ainvoke({}, {"configurable": {"principal": p}}))
