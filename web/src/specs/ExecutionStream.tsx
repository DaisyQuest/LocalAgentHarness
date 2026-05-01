/* ExecutionStream — live observability for spec-driven runs.
 *
 * Each chunk gets its own collapsible card. Inside the card:
 * - a sub-timeline of the planner-executor events for this chunk
 *   (plan, critique, step_start, step, done_check, token, warning)
 * - acceptance criteria with their verification status updating live
 * - a final summary with retry/blocked indicators
 *
 * The user can collapse completed chunks to keep the view focused on
 * what's running, but everything stays inspectable. The currently
 * running chunk auto-expands.
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AcceptanceCriterion, WorkChunk } from "../api";
import { Card, ChunkCard, CriterionRow, SegmentedProgress, StatusBadge, Chip } from "./components";

export type SpecEvent = {
  type: string;
  chunk_n?: number;
  [k: string]: any;
};

type ChunkBucket = {
  chunk: WorkChunk;
  events: SpecEvent[];
  criteria: Record<string, AcceptanceCriterion>;
  attempt: number;
  status: "queued" | "running" | "done" | "blocked";
};

export function ExecutionStream({ events, chunks, running }: {
  events: SpecEvent[];
  chunks: WorkChunk[];          // initial chunk list from spec.work_chunks (decompose result)
  running: boolean;
}) {
  const buckets = bucketEvents(events, chunks);
  const verifiedEv = events.find((e) => e.type === "spec_verified");
  const totalChunks = chunks.length;
  const doneCount = buckets.filter((b) => b.status === "done").length;
  const blockedCount = buckets.filter((b) => b.status === "blocked").length;

  const statuses = chunks.map((c) => c.status);
  return (
    <div className="space-y-4 animate-slide-up">
      <div className="rounded-xl border border-ink-700 bg-ink-800/40 p-4">
        <div className="flex items-center gap-3 mb-2">
          <span className="text-xs uppercase tracking-[0.12em] text-fg-mute">Live execution</span>
          {running && <span className="text-xs text-accent flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-ring" />
            streaming
          </span>}
          <div className="flex-1" />
          <span className="text-xs text-fg-mute font-mono tabular-nums">
            <span className="text-fg">{doneCount}</span>/<span>{totalChunks}</span>
            {blockedCount > 0 && <span className="text-danger-soft ml-2"> ⊘ {blockedCount} blocked</span>}
          </span>
        </div>
        <SegmentedProgress statuses={statuses} pulseRunning={running} />
        {running && <div className="h-px mt-2 animate-shimmer" />}
      </div>

      <div className="space-y-3">
        {buckets.map((b, i) => (
          <ChunkExecution
            key={b.chunk.n}
            bucket={b}
            autoOpen={b.status === "running" || b.status === "blocked" || (i === buckets.length - 1 && running)}
          />
        ))}
      </div>

      {verifiedEv && <VerificationCard verification={verifiedEv.verification} />}
    </div>
  );
}

function bucketEvents(events: SpecEvent[], baseChunks: WorkChunk[]): ChunkBucket[] {
  // Index initial chunks by n; we'll mutate bucket state as events arrive.
  const map = new Map<number, ChunkBucket>();
  for (const c of baseChunks) {
    map.set(c.n, {
      chunk: { ...c },
      events: [],
      criteria: Object.fromEntries(c.acceptance.map((a) => [a.id, { ...a }])),
      attempt: c.attempts || 0,
      status: c.status === "completed" ? "done"
            : c.status === "blocked" ? "blocked"
            : c.status === "in_progress" ? "running"
            : "queued",
    });
  }
  for (const ev of events) {
    if (ev.chunk_n == null) continue;
    const b = map.get(ev.chunk_n);
    if (!b) continue;
    if (ev.type === "chunk_start") {
      b.attempt = ev.attempt ?? 1;
      b.status = "running";
      b.chunk = { ...b.chunk, ...ev.chunk, status: "in_progress" };
    } else if (ev.type === "chunk_done") {
      b.status = "done";
      b.chunk = { ...b.chunk, ...ev.chunk, status: "completed" };
    } else if (ev.type === "chunk_blocked") {
      b.status = "blocked";
      b.chunk = { ...b.chunk, ...ev.chunk, status: "blocked" };
    } else if (ev.type === "chunk_retry") {
      b.status = "running";
    } else if (ev.type === "criterion_verified") {
      const cr = ev.criterion as AcceptanceCriterion;
      b.criteria[cr.id] = cr;
    } else {
      b.events.push(ev);
    }
  }
  // attach mutated criteria back to chunk for rendering
  for (const b of map.values()) {
    b.chunk = { ...b.chunk, acceptance: Object.values(b.criteria) };
  }
  return [...map.values()].sort((a, b) => a.chunk.n - b.chunk.n);
}

function ChunkExecution({ bucket, autoOpen }: { bucket: ChunkBucket; autoOpen: boolean }) {
  const [open, setOpen] = useState(autoOpen);
  useEffect(() => { if (autoOpen) setOpen(true); }, [autoOpen]);
  const { chunk, events, status, attempt } = bucket;
  const tone = status === "done" ? "ok"
            : status === "blocked" ? "danger"
            : status === "running" ? "accent"
            : "default";

  const allMet = chunk.acceptance.length > 0 && chunk.acceptance.every((a) => a.met === true);
  return (
    <Card
      tone={tone}
      title={
        <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 hover:text-fg transition">
          <span aria-hidden>{open ? "▾" : "▸"}</span>
          <span className="font-mono normal-case tracking-normal text-fg-mute">#{chunk.n}</span>
        </button>
      }
      right={
        <div className="flex items-center gap-2">
          {attempt > 1 && <Chip tone="warn">↻ retry {attempt - 1}</Chip>}
          <StatusBadge status={chunk.status} pulse={status === "running"} />
        </div>
      }
    >
      <div className="font-semibold text-fg leading-tight">{chunk.title}</div>
      <div className="text-sm text-fg-mute mt-1 mb-3 leading-relaxed">{chunk.description}</div>

      {/* acceptance — the headline. No dead "(n/n)" label; the row glyphs speak. */}
      {chunk.acceptance.length > 0 && (
        <div className="rounded-lg border border-ink-700 bg-ink-950/40 p-3 mb-3">
          {chunk.acceptance.map((ac) => (
            <CriterionRow key={ac.id} ac={ac} hideVerification={allMet && ac.met === true} />
          ))}
        </div>
      )}

      {/* event log, collapsible */}
      {open && events.length > 0 && (
        <div className="space-y-2 pl-3 border-l-2 border-ink-700">
          {events.map((ev, i) => <EventRow key={i} ev={ev} />)}
        </div>
      )}

      {chunk.last_error && status === "blocked" && (
        <div className="mt-2 text-xs text-danger-soft bg-danger/5 border border-danger/30 rounded-lg p-2">
          <span className="uppercase tracking-wider text-[10px] mr-1">error</span>
          {chunk.last_error}
        </div>
      )}
    </Card>
  );
}

function EventRow({ ev }: { ev: SpecEvent }) {
  switch (ev.type) {
    case "plan":      return <PlanEvent plan={ev.plan} />;
    case "plan_revised": return <PlanEvent plan={ev.plan} revised />;
    case "critique":  return <CritiqueEvent critique={ev.critique} />;
    case "step_start": return <StepStart step={ev.step} />;
    case "step":      return <StepResult result={ev.result} />;
    case "done_check": return <DoneCheckEvent dc={ev.done_check} />;
    case "todos":     return <TodosEvent todos={ev.todos} />;
    case "token":     return null; // streamed below
    case "warning":   return (
      <div className="text-xs text-warn-soft">
        ⚠ <span className="uppercase tracking-wider text-[10px] text-warn">{ev.stage}</span> {ev.error}
      </div>
    );
    case "reframe":   return null; // disabled per-chunk in spec mode
    default:          return null;
  }
}

function PlanEvent({ plan, revised }: { plan: any; revised?: boolean }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-fg-mute hover:text-fg list-none">
        <span className="text-fg-dim mr-1.5">▸</span>
        plan{revised ? " (revised)" : ""} · {plan.steps.length} step{plan.steps.length === 1 ? "" : "s"}
      </summary>
      <ol className="mt-1 ml-5 space-y-0.5 text-xs">
        {plan.steps.map((s: any) => (
          <li key={s.n} className="text-fg-mute">
            <span className="text-accent-soft font-mono mr-1">{s.n}.</span>
            {s.description}
            {s.tool && <span className="text-fg-dim font-mono ml-2">→ {s.tool}</span>}
          </li>
        ))}
      </ol>
    </details>
  );
}

function CritiqueEvent({ critique }: { critique: any }) {
  if (!critique.issues?.length) {
    return <div className="text-xs text-ok-soft">✓ critique clean — {critique.summary}</div>;
  }
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-fg-mute hover:text-fg list-none">
        <span className="text-fg-dim mr-1.5">▸</span>
        critique · <span className={critique.verdict === "revise" ? "text-warn-soft" : "text-fg-mute"}>
          {critique.verdict}
        </span> · {critique.issues.length} issue{critique.issues.length === 1 ? "" : "s"}
      </summary>
      <ul className="mt-1 ml-5 space-y-1 text-xs">
        {critique.issues.map((iss: any, i: number) => (
          <li key={i} className="flex gap-2">
            <span className={`shrink-0 px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wide ${
              iss.severity === "high"   ? "bg-danger/20 text-danger-soft" :
              iss.severity === "medium" ? "bg-warn/20 text-warn-soft" :
              "bg-ink-700 text-fg-dim"
            }`}>{iss.severity}</span>
            <span className="text-fg-mute">{iss.concern}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function StepStart({ step }: { step: any }) {
  return (
    <div className="text-xs text-fg-mute flex items-center gap-1.5">
      <span className="text-accent-soft font-mono">{step.n}.</span>
      <span className="flex-1 truncate">{step.description}</span>
      {step.tool && <code className="text-fg-dim font-mono text-[10px]">{step.tool}</code>}
    </div>
  );
}

function StepResult({ result }: { result: any }) {
  const tr = result.tool_result;
  if (!tr) {
    return result.notes ? (
      <div className="text-xs text-fg-dim italic ml-4">{result.notes}</div>
    ) : null;
  }
  const ok = tr.ok;
  const cached = tr.meta?.cached;
  return (
    <details className="text-xs ml-2">
      <summary className={`cursor-pointer list-none flex items-center gap-1.5 ${ok ? "text-ok-soft" : "text-danger-soft"}`}>
        <span className="text-fg-dim mr-1">▸</span>
        <span>{ok ? "✓" : "✗"}</span>
        <span className="text-fg-mute font-mono">{result.step.tool}</span>
        {cached && <Chip tone="default">cached</Chip>}
      </summary>
      <pre className="mt-1 ml-4 text-[11px] font-mono text-fg-mute bg-ink-950/60 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-48 border border-ink-700">
        {tr.output || tr.error}
      </pre>
    </details>
  );
}

function DoneCheckEvent({ dc }: { dc: any }) {
  const tone = dc.overall === "complete" ? "ok" : dc.overall === "partial" ? "warn" : "danger";
  const cls = tone === "ok" ? "text-ok-soft" : tone === "warn" ? "text-warn-soft" : "text-danger-soft";
  return (
    <details className="text-sm">
      <summary className={`cursor-pointer list-none ${cls}`}>
        <span className="text-fg-dim mr-1.5">▸</span>
        done-check · <span className="uppercase tracking-wide text-xs">{dc.overall}</span>
      </summary>
      <ul className="mt-1 ml-5 space-y-0.5 text-xs">
        {dc.criteria?.map((c: any, i: number) => (
          <li key={i} className="flex gap-2">
            <span className={c.met ? "text-ok" : "text-danger"}>{c.met ? "✓" : "✗"}</span>
            <span className="text-fg-mute">{c.description}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function TodosEvent({ todos }: { todos: any }) {
  const items = todos?.items ?? [];
  if (items.length === 0) return null;
  const p = todos.progress;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-fg-mute hover:text-fg list-none">
        <span className="text-fg-dim mr-1.5">▸</span>
        todos · {p.completed}/{p.total}
      </summary>
      <ul className="mt-1 ml-5 space-y-0.5">
        {items.map((it: any) => {
          const mark = it.status === "completed" ? "[x]"
                    : it.status === "in_progress" ? "[~]"
                    : it.status === "blocked" ? "[!]"
                    : "[ ]";
          const cls = it.status === "completed" ? "text-fg-dim line-through"
                    : it.status === "in_progress" ? "text-accent-soft"
                    : it.status === "blocked" ? "text-danger-soft"
                    : "text-fg-mute";
          return <li key={it.n} className={cls}><span className="font-mono mr-1">{mark}</span>{it.content}</li>;
        })}
      </ul>
    </details>
  );
}

// ── final scorecard ─────────────────────────────────────────

function VerificationCard({ verification }: { verification: any }) {
  const tone = verification.overall === "verified" ? "ok"
            : verification.overall === "partial" ? "warn"
            : "danger";
  const headline = {
    verified: "Spec verified.",
    partial:  "Partially verified.",
    failed:   "Verification failed.",
  }[verification.overall as "verified" | "partial" | "failed"];

  return (
    <Card
      tone={tone}
      title="final verification"
      right={<StatusBadge status={verification.overall} />}
    >
      <div className="text-lg font-semibold mb-3">{headline}</div>
      <div className="grid grid-cols-2 gap-3 mb-3">
        <Stat label="chunks completed" value={`${verification.chunks_completed} / ${verification.chunks_total}`} />
        <Stat label="criteria met"     value={`${verification.criteria_met} / ${verification.criteria_total}`} />
      </div>
      {verification.gaps?.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-fg-dim mb-1">gaps</div>
          <ul className="space-y-1 text-sm text-fg-mute">
            {verification.gaps.map((g: string, i: number) => (
              <li key={i} className="flex gap-2">
                <span className="text-danger-soft">·</span>
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-ink-950/40 border border-ink-700 p-3">
      <div className="text-[10px] uppercase tracking-wider text-fg-dim">{label}</div>
      <div className="text-xl font-mono mt-0.5">{value}</div>
    </div>
  );
}
