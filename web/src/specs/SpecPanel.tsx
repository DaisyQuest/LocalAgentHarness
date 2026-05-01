/* SpecPanel — top-level UI for spec-driven development.
 *
 * Two views: a list (left rail of saved specs) and a runner (the active
 * spec). The runner walks the user through every phase: review the
 * generated draft, answer ranked questions, watch decomposition, and
 * observe live execution + verification — all themeable, all observable.
 *
 * Auto-load logic kicks in when the user picks a spec or creates a new one;
 * we re-fetch the spec record after each phase so the UI reflects what's
 * persisted on disk (the source of truth).
 */
import { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import { Card, ChunkCard, EmptyState, SegmentedProgress, StatusBadge } from "./components";
import { PhaseTimeline } from "./PhaseTimeline";
import { InterrogationPanel } from "./Interrogation";
import { ExecutionStream, type SpecEvent } from "./ExecutionStream";

// Format a unix timestamp as human-relative ("4m ago", "yesterday")
function relativeTime(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}

export function SpecPanel() {
  const [rows, setRows] = useState<api.SpecRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ goal: string; rounds: number } | null>({ goal: "", rounds: 3 });

  async function refresh() {
    try { setRows(await api.listSpecs()); } catch { /* ignore */ }
  }
  useEffect(() => { refresh(); }, []);

  return (
    <div className="flex-1 flex overflow-hidden">
      <SpecSidebar
        rows={rows}
        activeId={activeId}
        onPick={(id) => { setActiveId(id); setDraft(null); }}
        onNew={() => { setActiveId(null); setDraft({ goal: "", rounds: 3 }); }}
        onDelete={async (id) => { await api.deleteSpec(id); if (activeId === id) setActiveId(null); refresh(); }}
      />
      <div className="flex-1 overflow-y-auto">
        {activeId ? (
          <SpecRunner
            sid={activeId}
            onChange={refresh}
            onClose={() => { setActiveId(null); setDraft({ goal: "", rounds: 3 }); }}
          />
        ) : draft ? (
          <NewSpecForm
            draft={draft}
            setDraft={setDraft}
            onCreated={async (sid) => { setActiveId(sid); await refresh(); }}
          />
        ) : (
          <EmptyState
            icon="✦"
            title="no spec selected"
            hint="pick a spec from the left, or start a new one."
            action={
              <button onClick={() => setDraft({ goal: "", rounds: 3 })}
                className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft text-white shadow-glow">
                + new spec
              </button>
            }
          />
        )}
      </div>
    </div>
  );
}

// ── sidebar ─────────────────────────────────────────────────

function SpecSidebar({ rows, activeId, onPick, onNew, onDelete }: {
  rows: api.SpecRow[]; activeId: string | null;
  onPick: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  return (
    <aside className="w-72 shrink-0 border-r border-ink-700 bg-ink-900/40 backdrop-blur p-3 overflow-y-auto">
      <button onClick={onNew} className="w-full mb-3 px-3 py-2 rounded-lg bg-accent hover:bg-accent-soft transition text-white font-medium shadow-glow">
        + new spec
      </button>
      <div className="text-[10px] uppercase tracking-wider text-fg-dim mb-1 px-1">specs</div>
      {rows.length === 0 && (
        <div className="text-xs text-fg-dim p-3">none yet — start one to begin.</div>
      )}
      <div className="space-y-1">
        {rows.map((r) => {
          const active = r.id === activeId;
          return (
            <div
              key={r.id}
              className={`group rounded-lg transition cursor-pointer ${
                active ? "bg-ink-800 ring-1 ring-accent/40" : "hover:bg-ink-800/60"
              }`}
              onClick={() => onPick(r.id)}
            >
              <div className="px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-fg truncate font-medium">{r.title || "(untitled)"}</div>
                    <div className="text-[11px] text-fg-mute flex gap-2 mt-0.5 items-center">
                      <span className="tabular-nums">{r.chunks} chunk{r.chunks === 1 ? "" : "s"}</span>
                      <span className="text-fg-dim">·</span>
                      <span className="text-fg-dim tabular-nums">{relativeTime(r.updated_at)}</span>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <StatusBadge status={r.status} />
                    <button
                      onClick={(e) => { e.stopPropagation(); if (confirm(`delete "${r.title}"?`)) onDelete(r.id); }}
                      className="opacity-0 group-hover:opacity-100 px-1 text-fg-dim hover:text-danger transition text-xs"
                      aria-label={`delete ${r.title}`}
                    >×</button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// ── new spec form ───────────────────────────────────────────

function NewSpecForm({ draft, setDraft, onCreated }: {
  draft: { goal: string; rounds: number };
  setDraft: (d: { goal: string; rounds: number } | null) => void;
  onCreated: (sid: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function start() {
    if (!draft.goal.trim()) return;
    setBusy(true); setErr(null);
    try {
      const spec = await api.startSpec(draft.goal.trim(), { max_rounds: draft.rounds });
      onCreated(spec.id);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-12 space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Spec-Driven Development</h2>
        <p className="text-fg-mute mt-1">
          Describe what you want. The agent interrogates you to lock the spec down,
          decomposes it into bite-sized chunks with explicit acceptance criteria,
          executes each, and verifies against your real workspace.
        </p>
      </div>

      <Card title="goal" tone="accent">
        <textarea
          value={draft.goal}
          onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
          rows={5}
          placeholder="e.g., add a dark mode toggle to settings; persists across reloads via localStorage; uses CSS variables only"
          className="w-full bg-ink-900 border border-ink-700 focus:border-accent focus:outline-none rounded-lg p-3 text-sm leading-relaxed resize-none"
        />
        <div className="flex items-center gap-3 mt-3">
          <label className="text-xs text-fg-dim flex items-center gap-2">
            interrogation rounds
            <input type="number" min={1} max={5} value={draft.rounds}
              onChange={(e) => setDraft({ ...draft, rounds: Math.max(1, Math.min(5, parseInt(e.target.value || "3", 10)))})}
              className="w-12 bg-ink-900 border border-ink-700 rounded px-2 py-0.5 text-fg font-mono text-xs"
            />
          </label>
          <div className="flex-1" />
          <button onClick={() => setDraft(null)} className="px-3 py-1.5 rounded-lg text-sm border border-ink-700 hover:bg-ink-800 text-fg-mute">cancel</button>
          <button onClick={start} disabled={busy || !draft.goal.trim()}
            className="px-4 py-1.5 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 transition shadow-glow text-white text-sm font-medium">
            {busy ? "drafting…" : "draft spec"}
          </button>
        </div>
        {err && <div className="mt-3 text-xs text-danger-soft border border-danger/30 bg-danger/5 rounded-lg p-2">{err}</div>}
      </Card>

      <div className="text-xs text-fg-dim">
        <span className="uppercase tracking-wider">how it works</span>
        <ol className="mt-2 space-y-1 list-decimal pl-5">
          <li>The agent reads your goal and structures it into requirements, constraints, and out-of-scope items.</li>
          <li>You answer ranked clarifying questions — binary, multiple-choice, or name-the-value. No open-ended fluff.</li>
          <li>The spec is decomposed into bite-sized chunks, each with explicit acceptance criteria.</li>
          <li>Each chunk runs through the planner-executor; the verifier checks each criterion against the real workspace.</li>
          <li>You watch every step. Cached reads, plan revisions, retries, blockers — all visible.</li>
        </ol>
      </div>
    </div>
  );
}

// ── runner ──────────────────────────────────────────────────

function SpecRunner({ sid, onChange, onClose }: { sid: string; onChange: () => void; onClose: () => void }) {
  const [spec, setSpec] = useState<api.Spec | null>(null);
  const [questions, setQuestions] = useState<api.ClarifyingQuestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [events, setEvents] = useState<SpecEvent[]>([]);
  const [running, setRunning] = useState(false);

  async function load() {
    setErr(null);
    try {
      const s = await api.getSpec(sid);
      setSpec(s);
      setQuestions(s.open_questions || []);
    } catch (e: any) { setErr(String(e.message || e)); }
  }
  // Switching specs must blow away in-memory event stream from the previous
  // run — otherwise spec B renders spec A's events.
  useEffect(() => {
    load();
    setEvents([]);
    setRunning(false);
  }, [sid]);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(label); setErr(null);
    try { return await fn(); }
    catch (e: any) { setErr(String(e.message || e)); return null; }
    finally { setBusy(null); onChange(); }
  }

  if (!spec) return <div className="p-8 text-fg-dim">{err ?? "loading…"}</div>;

  const phase = currentPhase(spec);
  const canAskMore = spec.rounds < 3 && !["executing", "verified", "partial", "failed"].includes(spec.status);

  return (
    <div className="flex flex-col">
      <PhaseTimeline spec={spec} />

      <div className="px-6 py-6 max-w-4xl mx-auto w-full space-y-6">
        {/* HEADER */}
        <div className="flex items-start gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap mb-1">
              <StatusBadge status={spec.status} pulse={spec.status === "executing"} />
              <code className="text-[11px] text-fg-dim font-mono">{spec.id}</code>
            </div>
            <h2 className="text-3xl font-semibold tracking-tight leading-tight">{spec.title}</h2>
            <div className="text-sm text-fg-mute mt-2 leading-relaxed max-w-2xl">"{spec.goal}"</div>
          </div>
          <button onClick={onClose}
            className="text-fg-dim hover:text-fg text-sm border border-ink-700 hover:border-ink-600 rounded-lg px-3 py-1 transition">
            close
          </button>
        </div>

        {err && <div className="text-sm text-danger-soft border border-danger/30 bg-danger/5 rounded-lg p-3">{err}</div>}

        {/* SPEC BODY */}
        <SpecBody spec={spec} />

        {/* PHASE-SPECIFIC BODY */}
        {phase === "interrogate" && (
          <>
            {questions.length === 0 ? (
              <Card tone="default" title="ask round">
                <div className="text-sm text-fg-mute mb-3">
                  No questions in flight. Ready to interrogate? The agent will produce up to 5
                  ranked questions — binary, choice, or name-the-value.
                </div>
                <div className="flex gap-2">
                  {canAskMore && (
                    <button
                      onClick={() => run("asking", async () => {
                        const { questions: qs, spec: s } = await api.specQuestions(sid);
                        setQuestions(qs); setSpec(s);
                        if (qs.length === 0) {
                          // model deemed spec clear → bump to readiness
                          await api.specReadiness(sid);
                          await load();
                        }
                      })}
                      disabled={!!busy}
                      className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 text-white text-sm shadow-glow">
                      {busy === "asking" ? "thinking…" : `ask round ${spec.rounds + 1}`}
                    </button>
                  )}
                  <button
                    onClick={() => run("ship", async () => { await api.specForceReady(sid, "user override"); await load(); })}
                    disabled={!!busy}
                    className="px-3 py-2 rounded-lg border border-warn/40 text-warn-soft hover:bg-warn/10 text-sm">
                    /ship it
                  </button>
                  {spec.readiness && (
                    <div className="ml-auto text-xs text-fg-dim self-center">
                      readiness: {spec.readiness.score}/5 {spec.readiness.summary && `· ${spec.readiness.summary}`}
                    </div>
                  )}
                </div>
              </Card>
            ) : (
              <InterrogationPanel
                questions={questions}
                rounds={spec.rounds}
                maxRounds={3}
                busy={!!busy}
                onSubmit={(answers) => run("answering", async () => {
                  const newSpec = await api.specAnswer(sid, answers);
                  setSpec(newSpec); setQuestions([]);
                  // auto-check readiness after each integration
                  await api.specReadiness(sid);
                  await load();
                })}
                onShip={() => run("ship", async () => { await api.specForceReady(sid, "user override"); await load(); })}
              />
            )}
          </>
        )}

        {phase === "decompose" && (
          <Card tone="info" title="ready · decompose into chunks">
            <div className="text-sm text-fg-mute mb-3">
              The spec looks ironclad. The agent will now split it into bite-sized
              chunks, each with explicit acceptance criteria.
            </div>
            <button
              onClick={() => run("decomposing", async () => { await api.specDecompose(sid); await load(); })}
              disabled={!!busy}
              className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 text-white shadow-glow text-sm">
              {busy === "decomposing" ? "decomposing…" : "decompose"}
            </button>
          </Card>
        )}

        {(phase === "execute" || phase === "verify") && spec.work_chunks.length > 0 && (
          <ExecutePhase
            spec={spec}
            events={events}
            running={running}
            onRun={async () => {
              setRunning(true); setEvents([]); setErr(null);
              try {
                for await (const ev of api.specExecute(sid, true)) {
                  setEvents((es) => [...es, ev]);
                }
              } catch (e: any) {
                setErr(String(e.message || e));
              } finally {
                setRunning(false);
                await load();
                onChange();
              }
            }}
          />
        )}
      </div>
    </div>
  );
}

// ── phase helpers ───────────────────────────────────────────

type Phase = "draft" | "interrogate" | "decompose" | "execute" | "verify";
function currentPhase(spec: api.Spec): Phase {
  if (spec.status === "verified" || spec.status === "partial" || spec.status === "failed") return "verify";
  if (spec.status === "executing") return "execute";
  if (spec.status === "ready" && spec.work_chunks.length > 0) return "execute";
  if (spec.status === "ready") return "decompose";
  if (spec.rounds > 0 || spec.open_questions.length > 0) return "interrogate";
  return "draft";
}

// ── body sub-views ──────────────────────────────────────────

function SpecBody({ spec }: { spec: api.Spec }) {
  return (
    <div className="grid gap-3">
      {spec.summary && (
        <Card title="summary" tone="accent">
          <p className="text-sm leading-relaxed text-fg">{spec.summary}</p>
        </Card>
      )}
      <div className="grid md:grid-cols-3 gap-3">
        <BulletCard title="requirements" items={spec.requirements} tone="ok" empty="(none yet)" />
        <BulletCard title="constraints" items={spec.constraints} tone="warn" empty="(none)" />
        <BulletCard title="out of scope" items={spec.out_of_scope} tone="default" empty="(none)" />
      </div>
      {spec.global_acceptance.length > 0 && (
        <Card title="global acceptance" tone="default">
          {spec.global_acceptance.map((ac) => (
            <div key={ac.id} className="py-1">
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full ${
                  ac.met === true ? "bg-ok" : ac.met === false ? "bg-danger" : "bg-fg-dim"
                }`} />
                <span className="text-sm">{ac.text}</span>
              </div>
              {ac.verification && (
                <code className="block ml-3.5 text-[11px] font-mono text-fg-dim">{ac.verification}</code>
              )}
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

function BulletCard({ title, items, tone, empty }: { title: string; items: string[]; tone: "ok" | "warn" | "default"; empty: string }) {
  return (
    <Card title={title} tone={tone === "default" ? "default" : tone}>
      {items.length === 0 ? (
        <div className="text-xs text-fg-dim italic">{empty}</div>
      ) : (
        <ul className="text-sm space-y-1">
          {items.map((it, i) => (
            <li key={i} className="flex gap-2">
              <span className={`shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full ${
                tone === "ok" ? "bg-ok" : tone === "warn" ? "bg-warn" : "bg-fg-dim"
              }`} />
              <span className="text-fg">{it}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ── execute phase wrapper ───────────────────────────────────

function ExecutePhase({ spec, events, running, onRun }: {
  spec: api.Spec; events: SpecEvent[]; running: boolean; onRun: () => void;
}) {
  const verified = spec.status === "verified" || spec.status === "partial" || spec.status === "failed";
  const progress = useMemo(() => {
    const done = spec.work_chunks.filter((c) => c.status === "completed").length;
    const blocked = spec.work_chunks.filter((c) => c.status === "blocked").length;
    return { done, blocked, total: spec.work_chunks.length };
  }, [spec.work_chunks]);

  const statuses = spec.work_chunks.map((c) => c.status);

  return (
    <div className="space-y-4">
      <Card
        tone={verified ? (spec.status === "verified" ? "ok" : spec.status === "failed" ? "danger" : "warn") : "default"}
        title={
          <div className="flex items-baseline gap-2">
            <span>chunks</span>
            <span className="text-[10px] text-fg-dim normal-case tracking-normal">{spec.work_chunks.length} total</span>
          </div>
        }
        right={
          <div className="flex items-center gap-3">
            {!running && !verified && (
              <button onClick={onRun} className="px-4 py-1.5 rounded-lg bg-accent hover:bg-accent-soft text-white text-sm shadow-glow">
                ▶ execute
              </button>
            )}
            {!running && verified && (
              <button onClick={onRun} className="px-3 py-1.5 rounded-lg text-sm border border-ink-700 hover:border-accent text-fg-mute">
                ↻ re-run
              </button>
            )}
          </div>
        }
      >
        {/* Headline: counts FIRST, segmented bar SECOND. Each cell is one chunk. */}
        <div className="flex items-baseline gap-3 mb-2">
          <div className="text-2xl font-semibold tabular-nums leading-none">
            <span className="text-ok">{progress.done}</span>
            <span className="text-fg-dim mx-0.5">/</span>
            <span className="text-fg-mute">{progress.total}</span>
          </div>
          <span className="text-[10px] uppercase tracking-[0.12em] text-fg-mute">complete</span>
          {progress.blocked > 0 && (
            <span className="text-xs text-danger-soft flex items-center gap-1">
              <span aria-hidden>⊘</span> {progress.blocked} blocked
            </span>
          )}
          <div className="flex-1" />
        </div>
        <SegmentedProgress statuses={statuses} pulseRunning />
        <div className="grid gap-3 mt-5">
          {spec.work_chunks.map((c) => <ChunkCard key={c.n} chunk={c} active={c.status === "in_progress"} />)}
        </div>
      </Card>

      {(running || events.length > 0) && (
        <ExecutionStream events={events} chunks={spec.work_chunks} running={running} />
      )}
    </div>
  );
}
