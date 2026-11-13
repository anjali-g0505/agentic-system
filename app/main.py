"""FastAPI entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.db import close_pool, open_pool, pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await open_pool()
    yield
    await close_pool()


app = FastAPI(title="RuPay Credit Card on UPI - Chargeback Desk", lifespan=lifespan)


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


@app.get("/health")
async def health() -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM chunks")
        (chunks,) = await cur.fetchone()
    return {"status": "ok", "db": "ok", "chunks": chunks}
