"""Engine: orchestrator shared by all surfaces. Provider + storage + router + RAG + tools + agent + memory + strategies."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator


from .agent import PlannerExecutor
from .agent.spec import ClarifyingQuestion, Spec
from .agent.spec_driven import SpecDrivenAgent, SpecDrivenConfig
from .agent.spec_store import SpecStore
from .config import Settings, settings as default_settings
from .core import ChatRequest, Message
from .memory import MemoryStore, extract_candidates, extract_memories
from .providers import build_provider
from .rag import Retriever, VectorStore, ingest_file, ingest_folder, ingest_url
from .router import Router
from .storage import Store
from .strategies import StrategyStore
from .tools import ToolRegistry, build_default_registry

log = logging.getLogger("localagent.engine")


def _truncate_title(text: str, max_chars: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


class Engine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.provider = build_provider(self.settings.provider)
        self.router = Router(self.settings.models, provider=self.provider)
        self.store = Store(self.settings.db_path)
        self.vector_store = VectorStore(
            self.settings.data_dir / "vectors.db", dim=self.settings.rag.embed_dim
        )
        self.retriever = Retriever(self.provider, self.vector_store, self.settings.models.embed)
        self.tools: ToolRegistry = build_default_registry(self.settings.tools)
        self.tools.set_confirm_required(self.settings.tools.require_confirmation)
        self.strategies = StrategyStore(
            self.settings.data_dir / "strategies",
            project_root=self.settings.tools.workspace,
        )
        self.agent = PlannerExecutor(
            self.provider,
            self.tools,
            planner_model=self.settings.models.planner,
            executor_model=self.settings.models.executor,
            max_steps=self.settings.agent.max_steps,
            compose_strategy=self.strategies.compose,
            use_reframe=self.settings.agent.use_reframe,
            use_critique=self.settings.agent.use_critique,
            use_done_check=self.settings.agent.use_done_check,
            json_retries=self.settings.agent.json_retries,
            ambiguity_threshold=self.settings.agent.ambiguity_threshold,
        )
        self.memory = MemoryStore(
            self.provider,
            self.settings.models.embed,
            self.settings.memory,
            sqlite_path=self.settings.data_dir / "memory.db",
            dim=self.settings.rag.embed_dim,
        )
        self.spec_store = SpecStore(self.settings.data_dir / "specs")
        self.spec_agent = SpecDrivenAgent(
            self.provider,
            self.tools,
            planner=self.agent,
            spec_model=self.settings.models.planner,
            verifier_model=self.settings.models.fast,
            config=SpecDrivenConfig(),
        )
        # transient per-turn signals surfaced via response headers
        self.last_recall_count: int = 0
        self.last_rag_count: int = 0
        self.last_auto_save: list[dict[str, Any]] = []

    async def close(self) -> None:
        await self.provider.close()
        self.store.close()
        self.vector_store.close()
        self.memory.close()

    # ── settings (runtime-mutable subset) ────────────────────
    def _rebuild_runtime_components(self) -> None:
        """Rebuild components whose construction snapshots settings values.

        Long-lived connections (provider, stores) are NOT touched; restart for those.
        """
        self.tools = build_default_registry(self.settings.tools)
        self.tools.set_confirm_required(self.settings.tools.require_confirmation)
        self.router = Router(self.settings.models, provider=self.provider)
        self.retriever = Retriever(self.provider, self.vector_store, self.settings.models.embed)
        self.strategies.set_project_root(self.settings.tools.workspace)
        # spec_agent shares planner + tools; rebuild it so it picks up new tool registry
        if hasattr(self, "spec_agent"):
            self.spec_agent = SpecDrivenAgent(
                self.provider, self.tools,
                planner=self.agent,
                spec_model=self.settings.models.planner,
                verifier_model=self.settings.models.fast,
                config=self.spec_agent.config,
            )
        self.agent = PlannerExecutor(
            self.provider,
            self.tools,
            planner_model=self.settings.models.planner,
            executor_model=self.settings.models.executor,
            max_steps=self.settings.agent.max_steps,
            compose_strategy=self.strategies.compose,
            use_reframe=self.settings.agent.use_reframe,
            use_critique=self.settings.agent.use_critique,
            use_done_check=self.settings.agent.use_done_check,
            json_retries=self.settings.agent.json_retries,
            ambiguity_threshold=self.settings.agent.ambiguity_threshold,
        )

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge ``patch`` (partial nested dict) into live settings, persist, rebuild.

        Rejects fields that need a process restart (provider, data_dir).
        Returns the new resolved settings dict.
        """
        from .config import _deep_merge

        # Reject restart-required fields with a clear error
        forbidden = {"provider", "data_dir"}
        bad = [k for k in patch.keys() if k in forbidden]
        if bad:
            raise ValueError(
                f"these fields require a server restart and can't be hot-updated: {', '.join(bad)}"
            )

        current = self.settings.model_dump(mode="json")
        merged = _deep_merge(current, patch)
        # Validate via re-construction; raises ValidationError on bad input
        new_settings = type(self.settings)(**merged)
        self.settings = new_settings
        self.settings.save_overrides(patch)
        self._rebuild_runtime_components()
        return self.settings.model_dump(mode="json")

    # ── conversations ────────────────────────────────────────
    def new_conversation(self, title: str | None = None) -> str:
        cid = self.store.create_conversation(title=title)
        self.store.append_message(cid, Message(role="system", content=self.settings.system_prompt))
        return cid

    def delete_conversation(self, cid: str) -> None:
        self.store.delete_conversation(cid)

    async def prepare_send(
        self,
        conversation_id: str,
        user_text: str,
        *,
        role: str | None = None,
        use_rag: bool = False,
        use_memory: bool | None = None,
        rag_k: int | None = None,
    ) -> tuple[list[Message], str]:
        """Run the synchronous-ish prep work: persist user msg, auto-title, recall, RAG, route.

        Returns (request_messages, resolved_model). Sets engine.last_*_count signals so
        callers (HTTP) can surface them via response headers before streaming begins.
        """
        self.store.append_message(conversation_id, Message(role="user", content=user_text))
        if not self.store.get_title(conversation_id):
            self.store.set_title(conversation_id, _truncate_title(user_text))

        msgs = self.store.get_messages(conversation_id)
        transient: list[Message] = []

        recall_enabled = self.settings.memory.auto_recall if use_memory is None else use_memory
        self.last_recall_count = 0
        if recall_enabled:
            try:
                hits = await self.memory.recall(user_text)
                if hits:
                    transient.append(Message(role="system", content=MemoryStore.format_for_prompt(hits)))
                    self.last_recall_count = len(hits)
            except Exception as e:
                log.warning("memory recall failed: %s", e)

        self.last_rag_count = 0
        if use_rag:
            try:
                hits = await self.retriever.retrieve(user_text, k=rag_k or self.settings.rag.top_k)
                if hits:
                    ctx = self.retriever.format_context(hits)
                    transient.append(Message(role="system", content=f"Relevant context retrieved:\n{ctx}"))
                    self.last_rag_count = len(hits)
            except Exception as e:
                log.warning("rag retrieve failed: %s", e)

        strategy_block = self.strategies.compose("chat")
        if strategy_block:
            transient.append(Message(role="system", content=strategy_block))

        if msgs and msgs[0].role == "system" and transient:
            request_msgs = [msgs[0], *transient, *msgs[1:]]
        else:
            request_msgs = [*transient, *msgs]

        model = await self.router.route(role or self.settings.default_role, user_text)
        return request_msgs, model

    async def stream_response(
        self,
        conversation_id: str,
        request_msgs: list[Message],
        model: str,
        *,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Run inference, persist assistant turn, log telemetry, schedule auto-save."""
        req = ChatRequest(model=model, messages=request_msgs, temperature=temperature)
        full: list[str] = []
        usage = None
        async for chunk in self.provider.stream(req):
            if chunk.delta:
                full.append(chunk.delta)
                yield chunk.delta
            if chunk.done:
                usage = chunk.usage
        text = "".join(full)
        self.store.append_message(conversation_id, Message(role="assistant", content=text))
        if usage:
            self.store.log_telemetry(
                model=model, conversation_id=conversation_id,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                latency_ms=usage.latency_ms,
            )
        mem_cfg = self.settings.memory
        if mem_cfg.auto_save:
            try:
                turns = self.store.count_user_messages(conversation_id)
                if turns > 0 and turns % mem_cfg.auto_save_every_turns == 0:
                    asyncio.create_task(self._auto_save_memories(conversation_id))
            except Exception as e:
                log.warning("auto-save scheduling failed: %s", e)

    async def send(
        self,
        conversation_id: str,
        user_text: str,
        *,
        role: str | None = None,
        temperature: float = 0.7,
        use_rag: bool = False,
        use_memory: bool | None = None,
        rag_k: int | None = None,
    ) -> AsyncIterator[str]:
        request_msgs, model = await self.prepare_send(
            conversation_id, user_text,
            role=role, use_rag=use_rag, use_memory=use_memory, rag_k=rag_k,
        )
        async for d in self.stream_response(conversation_id, request_msgs, model, temperature=temperature):
            yield d

    async def _auto_save_memories(self, conversation_id: str) -> list[dict[str, Any]]:
        """Run extractor + dedup; save survivors. Stores result on engine for surfacing."""
        cfg = self.settings.memory
        try:
            msgs = self.store.get_messages(conversation_id)
            cands = await extract_candidates(
                self.provider,
                self.settings.models.memory_extractor,
                msgs,
                window=cfg.auto_save_window,
            )
            if not cands:
                self.last_auto_save = []
                return []
            results = await self.memory.remember_candidates(
                cands,
                min_importance=cfg.auto_save_min_importance,
                dedup_threshold=cfg.auto_save_dedup_threshold,
            )
            self.last_auto_save = results
            saved = sum(1 for r in results if r["action"] == "save")
            log.info("auto-memory: %d candidates → %d saved (cid=%s)", len(cands), saved, conversation_id[:8])
            return results
        except Exception as e:
            log.warning("auto-save failed: %s", e)
            return []

    async def extract_memories_now(self, conversation_id: str) -> list[dict[str, Any]]:
        """Synchronous-on-demand variant for the API + UI button."""
        return await self._auto_save_memories(conversation_id)

    async def end_conversation(self, conversation_id: str) -> list[str]:
        """Legacy: lightweight extractor used at conversation end. Kept for compat."""
        msgs = self.store.get_messages(conversation_id)
        items = await extract_memories(self.provider, self.settings.models.memory_extractor, msgs)
        ids: list[str] = []
        for item in items:
            ids.append(await self.memory.remember(item["text"], kind=item.get("kind", "fact")))
        return ids

    # ── agent ────────────────────────────────────────────────
    async def agent_run(self, goal: str) -> AsyncIterator[dict[str, Any]]:
        async for ev in self.agent.run(goal):
            yield ev

    # ── spec-driven development ──────────────────────────────
    async def spec_start(self, goal: str, *, title_hint: str | None = None) -> Spec:
        """Phase 1: draft + persist a fresh Spec from a free-form user goal."""
        spec = await self.spec_agent.draft(goal, title_hint=title_hint)
        return self.spec_store.save(spec)

    async def spec_questions(self, sid: str) -> tuple[Spec, list[ClarifyingQuestion]]:
        """Phase 2: produce the next round of clarifying questions for ``sid``."""
        spec = self.spec_store.load(sid)
        if not spec:
            raise ValueError(f"unknown spec: {sid}")
        questions = await self.spec_agent.ask_questions(spec)
        spec.open_questions = questions
        return self.spec_store.save(spec), questions

    async def spec_answer(self, sid: str, answers: list[dict[str, Any]]) -> Spec:
        """Phase 3: integrate ``[{n, answer}]`` into the spec.

        Looks up the matching open_question by ``n`` so the LLM gets the
        question metadata (kind, importance) when integrating.
        """
        spec = self.spec_store.load(sid)
        if not spec:
            raise ValueError(f"unknown spec: {sid}")
        qa: list[tuple[ClarifyingQuestion, str]] = []
        by_n = {q.n: q for q in spec.open_questions}
        for a in answers:
            n = int(a.get("n", 0))
            ans = str(a.get("answer", "")).strip()
            if not ans:
                continue
            q = by_n.get(n)
            if q is None:
                continue
            q.answer = ans
            qa.append((q, ans))
        spec = await self.spec_agent.integrate_answers(spec, qa)
        spec.open_questions = []
        return self.spec_store.save(spec)

    async def spec_readiness(self, sid: str) -> tuple[Spec, "Any"]:
        spec = self.spec_store.load(sid)
        if not spec:
            raise ValueError(f"unknown spec: {sid}")
        readiness = await self.spec_agent.assess_readiness(spec)
        if readiness.ready:
            spec.status = "ready"
        return self.spec_store.save(spec), readiness

    def spec_force_ready(self, sid: str, *, reason: str = "user override") -> Spec:
        spec = self.spec_store.load(sid)
        if not spec:
            raise ValueError(f"unknown spec: {sid}")
        self.spec_agent.force_ready(spec, reason=reason)
        return self.spec_store.save(spec)

    async def spec_decompose(self, sid: str) -> Spec:
        spec = self.spec_store.load(sid)
        if not spec:
            raise ValueError(f"unknown spec: {sid}")
        spec = await self.spec_agent.decompose(spec)
        return self.spec_store.save(spec)

    async def spec_execute(self, sid: str) -> AsyncIterator[dict[str, Any]]:
        spec = self.spec_store.load(sid)
        if not spec:
            yield {"type": "spec_error", "error": f"unknown spec: {sid}"}
            return

        def _save(s: Spec) -> None:
            self.spec_store.save(s)

        async for ev in self.spec_agent.execute(spec, on_save=_save):
            yield ev
        # Final state (verification already saved by execute via on_save)
        self.spec_store.save(spec)

    # ── rag ──────────────────────────────────────────────────
    async def rag_ingest_path(self, path: str | Path) -> list[str]:
        p = Path(path)
        kw = dict(
            provider=self.provider, store=self.vector_store, embed_model=self.settings.models.embed,
            chunk_size=self.settings.rag.chunk_size, overlap=self.settings.rag.chunk_overlap,
        )
        if p.is_dir():
            return await ingest_folder(p, **kw)
        return [await ingest_file(p, **kw)]

    async def rag_ingest_url(self, url: str) -> str:
        return await ingest_url(
            url, provider=self.provider, store=self.vector_store,
            embed_model=self.settings.models.embed,
            chunk_size=self.settings.rag.chunk_size, overlap=self.settings.rag.chunk_overlap,
        )
