"""FastAPI application — multi-bot launcher + approval API."""

import asyncio
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from starlette.status import HTTP_403_FORBIDDEN

from src.config import CONFIG, AI_PROVIDER, API_KEY
from src.adapters.llm.executor import create_executor

app = FastAPI(title="Smol Developer Claw Server")

executor = create_executor(AI_PROVIDER)

# ── API key authentication ──────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _verify_api_key(api_key: Optional[str] = Security(_api_key_header)):
    if not API_KEY:
        return  # no key configured — skip auth (dev mode)
    if api_key != API_KEY:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid API key")


# ── Models ──────────────────────────────────────────────────


class AskRequest(BaseModel):
    message: str


class AskResponse(BaseModel):
    response: str


class StatusResponse(BaseModel):
    sessionId: str
    aiProvider: str
    usage: Optional[Dict[str, Any]] = None


@app.post("/ask", response_model=AskResponse, dependencies=[Security(_verify_api_key)])
async def ask(request: AskRequest):
    """Manual question endpoint."""
    try:
        response = await executor.execute(request.message)
        return AskResponse(response=response)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/status", response_model=StatusResponse)
async def status():
    """Server status endpoint."""
    return StatusResponse(
        sessionId=CONFIG["session_id"],
        aiProvider=AI_PROVIDER,
        usage=executor.usage_tracker.get_status() if executor.usage_tracker else None,
    )


@app.get("/")
async def root():
    """Health check."""
    return {"status": "ok", "session": CONFIG["session_id"], "provider": AI_PROVIDER}


@app.on_event("startup")
async def startup_event():
    """Start multi-bot system on server startup."""
    print("Smol Developer Claw Server starting")
    print(f"Session: {CONFIG['session_id']}")

    from src.config import DISCORD_TOKENS
    has_multi_bot = any(DISCORD_TOKENS.values())

    if has_multi_bot:
        from src.adapters.discord.launcher import launch_all_bots
        print("Starting multi-bot system...")

        async def _start_multi_bots():
            try:
                await launch_all_bots()
            except Exception as e:
                print(f"Multi-bot system failed to start: {e}")

        asyncio.create_task(_start_multi_bots())
    else:
        print("Discord bots not configured (set DISCORD_*_TOKEN in .env)")

    print("Ready!")
