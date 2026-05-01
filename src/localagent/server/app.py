from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..engine import Engine


class ChatIn(BaseModel):
    conversation_id: str | None = None
    message: str
    role: str = "auto"
    temperature: float = 0.7
    use_rag: bool = False
    use_memory: bool | None = None


class IngestIn(BaseModel):
    path: str | None = None
    url: str | None = None


class AgentIn(BaseModel):
    goal: str
    auto_approve: bool = False  # if False, the server denies confirm-required tools


class MemoryIn(BaseModel):
    text: str
    kind: str = "fact"


_engine: Engine | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _engine
    _engine = Engine()
    try:
        yield
    finally:
        await _engine.close()


app = FastAPI(title="LocalAgent", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _e() -> Engine:
    if _engine is None:
        raise HTTPException(500, "engine not initialized")
    return _engine


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/models")
async def models():
    return {"models": await _e().provider.list_models()}


@app.get("/api/conversations")
async def conversations():
    return {"conversations": _e().store.list_conversations()}


@app.post("/api/conversations")
async def new_conversation():
    return {"id": _e().new_conversation()}


@app.get("/api/conversations/{cid}/messages")
async def conv_messages(cid: str):
    return {"messages": [m.model_dump() for m in _e().store.get_messages(cid)]}


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn):
    engine = _e()
    cid = body.conversation_id or engine.new_conversation()

    # Run prep synchronously so headers carry accurate recall/rag counts
    request_msgs, model = await engine.prepare_send(
        cid, body.message,
        role=body.role, use_rag=body.use_rag, use_memory=body.use_memory,
    )

    async def gen():
        async for delta in engine.stream_response(
            cid, request_msgs, model, temperature=body.temperature,
        ):
            yield delta

    headers = {
        "x-conversation-id": cid,
        "x-model": model,
        "x-memory-recalled": str(engine.last_recall_count),
        "x-rag-recalled": str(engine.last_rag_count),
    }
    return StreamingResponse(gen(), media_type="text/plain", headers=headers)


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    _e().delete_conversation(cid)
    return {"ok": True}


@app.post("/api/conversations/{cid}/extract-memories")
async def extract_memories_now(cid: str):
    return {"results": await _e().extract_memories_now(cid)}


@app.post("/api/agent/run")
async def agent_run(body: AgentIn):
    engine = _e()

    async def confirmer(name: str, args: dict[str, Any]) -> bool:
        return body.auto_approve

    engine.tools.set_confirmer(confirmer)

    async def gen():
        try:
            async for ev in engine.agent_run(body.goal):
                yield json.dumps(ev) + "\n"
        finally:
            engine.tools.set_confirmer(None)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/rag/documents")
async def rag_docs():
    return {"documents": _e().vector_store.list_documents()}


@app.post("/api/rag/ingest")
async def rag_ingest(body: IngestIn):
    engine = _e()
    if body.url:
        return {"id": await engine.rag_ingest_url(body.url)}
    if body.path:
        return {"ids": await engine.rag_ingest_path(body.path)}
    raise HTTPException(400, "provide path or url")


@app.delete("/api/rag/documents/{did}")
async def rag_delete(did: str):
    _e().vector_store.delete_document(did)
    return {"ok": True}


@app.get("/api/memory")
async def memory_list():
    return {"memories": _e().memory.list()}


@app.post("/api/memory")
async def memory_add(body: MemoryIn):
    return {"id": await _e().memory.remember(body.text, kind=body.kind)}


@app.delete("/api/memory/{mid}")
async def memory_delete(mid: str):
    _e().memory.forget(mid)
    return {"ok": True}


@app.get("/api/memory/search")
async def memory_search(q: str, k: int = 5):
    return {"hits": await _e().memory.recall(q, k=k)}


# ── strategies (master context) ──────────────────────────────
class StrategyIn(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    scopes: list[str] = ["all"]
    active: bool = True
    body: str = ""


@app.get("/api/strategies")
async def strategies_list():
    return {"strategies": [s.model_dump() for s in _e().strategies.list()]}


@app.get("/api/strategies/{sid}")
async def strategies_get(sid: str):
    s = _e().strategies.get(sid)
    if not s:
        raise HTTPException(404, "not found")
    return s.model_dump()


@app.post("/api/strategies")
async def strategies_upsert(body: StrategyIn):
    from ..strategies import Strategy
    s = Strategy(
        id=body.id or "",
        name=body.name,
        description=body.description,
        scopes=body.scopes,  # type: ignore[arg-type]
        active=body.active,
        body=body.body,
    )
    return _e().strategies.upsert(s).model_dump()


@app.post("/api/strategies/{sid}/active")
async def strategies_toggle(sid: str, active: bool = True):
    s = _e().strategies.set_active(sid, active)
    if not s:
        raise HTTPException(404, "not found")
    return s.model_dump()


@app.delete("/api/strategies/{sid}")
async def strategies_delete(sid: str):
    if not _e().strategies.delete(sid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/strategies/preview/{scope}")
async def strategies_preview(scope: str):
    return {"text": _e().strategies.compose(scope)}  # type: ignore[arg-type]


# ── settings (live + persisted) ──────────────────────────────
@app.get("/api/settings")
async def settings_get():
    """Current resolved settings + the persisted-overrides file path for transparency."""
    e = _e()
    return {
        "settings": e.settings.model_dump(mode="json"),
        "overrides_path": str(e.settings.overrides_path),
    }


@app.patch("/api/settings")
async def settings_patch(patch: dict[str, Any]):
    """Hot-update runtime-mutable settings. Returns the resolved new settings."""
    try:
        return {"settings": _e().update_settings(patch)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"invalid settings: {type(e).__name__}: {e}")


# ── spec-driven development ─────────────────────────────────


class SpecStartIn(BaseModel):
    goal: str
    title_hint: str | None = None
    max_rounds: int | None = None


class SpecAnswerIn(BaseModel):
    answers: list[dict[str, Any]]  # [{n, answer}]


class SpecForceReadyIn(BaseModel):
    reason: str = "user override"


@app.get("/api/specs")
async def specs_list():
    return {"specs": _e().spec_store.list()}


@app.get("/api/specs/{sid}")
async def specs_get(sid: str):
    s = _e().spec_store.load(sid)
    if not s:
        raise HTTPException(404, "not found")
    return s.model_dump()


@app.delete("/api/specs/{sid}")
async def specs_delete(sid: str):
    if not _e().spec_store.delete(sid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/specs")
async def specs_start(body: SpecStartIn):
    e = _e()
    if body.max_rounds is not None:
        e.spec_agent.config.max_rounds = body.max_rounds
    spec = await e.spec_start(body.goal, title_hint=body.title_hint)
    return spec.model_dump()


@app.post("/api/specs/{sid}/questions")
async def specs_questions(sid: str):
    try:
        spec, qs = await _e().spec_questions(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"spec": spec.model_dump(), "questions": [q.model_dump() for q in qs]}


@app.post("/api/specs/{sid}/answer")
async def specs_answer(sid: str, body: SpecAnswerIn):
    try:
        spec = await _e().spec_answer(sid, body.answers)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return spec.model_dump()


@app.post("/api/specs/{sid}/readiness")
async def specs_readiness(sid: str):
    try:
        spec, r = await _e().spec_readiness(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"spec": spec.model_dump(), "readiness": r.model_dump()}


@app.post("/api/specs/{sid}/ready")
async def specs_force_ready(sid: str, body: SpecForceReadyIn):
    try:
        spec = _e().spec_force_ready(sid, reason=body.reason)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return spec.model_dump()


@app.post("/api/specs/{sid}/decompose")
async def specs_decompose(sid: str):
    try:
        spec = await _e().spec_decompose(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return spec.model_dump()


@app.post("/api/specs/{sid}/execute")
async def specs_execute(sid: str, auto_approve: bool = True):
    e = _e()

    async def confirmer(name: str, args: dict[str, Any]) -> bool:
        return auto_approve

    e.tools.set_confirmer(confirmer)

    async def gen():
        try:
            async for ev in e.spec_execute(sid):
                yield json.dumps(ev) + "\n"
        finally:
            e.tools.set_confirmer(None)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ── static frontend (built React app), or redirect to Vite dev ─────
_web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
if _web_dist.exists():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
else:
    @app.get("/")
    async def _root():
        return RedirectResponse(url="http://localhost:5173/", status_code=307)
