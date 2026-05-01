/* Shared UI atoms for spec-driven views.
 *
 * Status badges, criterion checkmarks, importance pips, the phase-timeline
 * dot — all the small, themeable pieces re-used across the spec pages.
 * Color comes from the Tailwind tokens (`accent`, `ok`, `warn`, `danger`)
 * which resolve to CSS variables, so every theme just works.
 */
import { type ReactNode } from "react";
import type {
  AcceptanceCriterion,
  ChunkStatus,
  SpecStatus,
  WorkChunk,
} from "../api";

// ── status pills / dots ─────────────────────────────────────

// Each status carries a tone (color token), a short label, and a
// **non-color glyph** so the state survives in monochrome / colorblind
// contexts. The glyph is the same one used inside the numbered chunk
// circle when status drives the chunk-card identity.
const STATUS_TONE: Record<SpecStatus | ChunkStatus, { tone: string; label: string; glyph: string }> = {
  draft:       { tone: "fg-mute",         label: "draft",     glyph: "·"  },
  ready:       { tone: "info",            label: "ready",     glyph: "▸"  },
  executing:   { tone: "accent",          label: "running",   glyph: "◐"  },
  verified:    { tone: "ok",              label: "verified",  glyph: "✓"  },
  partial:     { tone: "warn",            label: "partial",   glyph: "◑"  },
  failed:      { tone: "danger",          label: "failed",    glyph: "✕"  },
  pending:     { tone: "fg-dim",          label: "pending",   glyph: "○"  },
  in_progress: { tone: "accent",          label: "running",   glyph: "◐"  },
  completed:   { tone: "ok",              label: "done",      glyph: "✓"  },
  blocked:     { tone: "danger",          label: "blocked",   glyph: "⊘"  },
  skipped:     { tone: "fg-dim",          label: "skipped",   glyph: "—"  },
};

// Tailwind needs full class names at build time; map tone → real classes
const TONE_BG: Record<string, string> = {
  "fg-mute":  "bg-ink-700 text-fg-mute",
  "fg-dim":   "bg-ink-800 text-fg-dim",
  "info":     "bg-info/15 text-info border-info/30",
  "accent":   "bg-accent/15 text-accent-soft border-accent/30",
  "ok":       "bg-ok/15 text-ok-soft border-ok/30",
  "warn":     "bg-warn/15 text-warn-soft border-warn/30",
  "danger":   "bg-danger/15 text-danger-soft border-danger/30",
};
const TONE_DOT: Record<string, string> = {
  "fg-mute":  "bg-ink-600",
  "fg-dim":   "bg-ink-700",
  "info":     "bg-info",
  "accent":   "bg-accent",
  "ok":       "bg-ok",
  "warn":     "bg-warn",
  "danger":   "bg-danger",
};

export function StatusBadge({ status, label, pulse }: { status: SpecStatus | ChunkStatus; label?: string; pulse?: boolean }) {
  const { tone, label: defLabel, glyph } = STATUS_TONE[status];
  const cls = TONE_BG[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] uppercase tracking-[0.08em] font-medium border border-transparent ${cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${TONE_DOT[tone]} ${pulse ? "animate-pulse-ring" : ""}`} aria-hidden />
      <span className="font-mono" aria-hidden>{glyph}</span>
      {label ?? defLabel}
    </span>
  );
}

// ── importance pips ─────────────────────────────────────────

export function ImportancePips({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" title={`importance ${value}/5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={`w-1 h-3 rounded-sm ${
            i <= value ? "bg-accent" : "bg-ink-700"
          }`}
        />
      ))}
    </span>
  );
}

// ── criterion check ─────────────────────────────────────────

export function CriterionRow({ ac, hideVerification = false }: { ac: AcceptanceCriterion; hideVerification?: boolean }) {
  const icon = ac.met === true ? "✓" : ac.met === false ? "✕" : "○";
  const iconCls =
    ac.met === true  ? "bg-ok/20 text-ok ring-1 ring-ok/40"
  : ac.met === false ? "bg-danger/20 text-danger-soft ring-1 ring-danger/40"
  :                    "bg-ink-800 text-fg-dim ring-1 ring-ink-700";
  return (
    <div className="flex gap-2.5 items-start py-1.5">
      <span className={`shrink-0 mt-0.5 w-4 h-4 rounded-full text-[10px] flex items-center justify-center font-bold ${iconCls}`} aria-label={ac.met === true ? "met" : ac.met === false ? "not met" : "pending"}>
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-fg leading-snug">{ac.text}</div>
        {/* Hide the prescriptive grep/glob command on rows that already passed —
            it's pure noise on green. Keep visible for not-yet-met or failed. */}
        {ac.verification && !hideVerification && (
          <code className="block text-[11px] font-mono text-fg-mute mt-0.5">{ac.verification}</code>
        )}
        {ac.evidence && (
          <div className={`mt-0.5 text-xs ${ac.met ? "text-ok-soft" : "text-danger-soft"} italic leading-snug`}>
            {ac.evidence}
          </div>
        )}
      </div>
    </div>
  );
}

// ── progress bar ────────────────────────────────────────────

/** Thin scalar bar — used in tight headers where a segmented bar is overkill. */
export function ProgressBar({ done, total, blocked = 0 }: { done: number; total: number; blocked?: number }) {
  const t = Math.max(1, total);
  const pctDone    = (done / t) * 100;
  const pctBlocked = (blocked / t) * 100;
  return (
    <div className="w-full h-2 rounded-full bg-ink-800 overflow-hidden flex">
      <div className="bg-ok transition-all duration-300" style={{ width: `${pctDone}%` }} />
      <div className="bg-danger/70" style={{ width: `${pctBlocked}%` }} />
    </div>
  );
}

/** Segmented progress — one cell per chunk, color-coded by status.
 *  At-a-glance the user sees: 5 cells, 2 green, 1 violet pulsing, 1 grey, 1 red.
 *  This is the headline; the numeric `2 / 5` underneath is just the gloss.
 *
 *  Pending cells use a ring + low fill rather than opacity-30 (which made the
 *  cell vanish on low-contrast themes like terminal and arctic). The result
 *  reads as "slot reserved" without disappearing. */
export function SegmentedProgress({ statuses, pulseRunning = true }: { statuses: ChunkStatus[]; pulseRunning?: boolean }) {
  if (statuses.length === 0) {
    return <div className="h-2 rounded-full bg-ink-800" />;
  }
  return (
    <div className="flex gap-1.5">
      {statuses.map((s, i) => {
        const isPending = s === "pending" || s === "skipped";
        const tone = STATUS_TONE[s].tone;
        const fill = isPending
          ? "bg-ink-700 ring-1 ring-inset ring-ink-600"
          : TONE_DOT[tone];
        const pulse = pulseRunning && s === "in_progress" ? "animate-pulse-ring" : "";
        return (
          <span
            key={i}
            className={`flex-1 h-2 rounded-full ${fill} ${pulse} transition-all duration-300`}
            title={`#${i + 1} · ${STATUS_TONE[s].label}`}
            aria-label={`chunk ${i + 1} ${STATUS_TONE[s].label}`}
          />
        );
      })}
    </div>
  );
}

// ── card scaffolding ────────────────────────────────────────

export function Card({ title, right, children, tone }: { title?: ReactNode; right?: ReactNode; children: ReactNode; tone?: "default" | "accent" | "ok" | "warn" | "danger" | "info" }) {
  const border = tone === "accent" ? "border-accent/30 bg-accent/5"
    : tone === "ok"     ? "border-ok/30 bg-ok/5"
    : tone === "warn"   ? "border-warn/30 bg-warn/5"
    : tone === "danger" ? "border-danger/30 bg-danger/5"
    : tone === "info"   ? "border-info/30 bg-info/5"
    : "border-ink-700 bg-ink-800/40";
  return (
    <div className={`rounded-xl border p-4 ${border} animate-slide-up`}>
      {(title || right) && (
        <div className="flex items-center gap-3 mb-3">
          {title && <div className="text-xs uppercase tracking-wider text-fg-dim">{title}</div>}
          <div className="flex-1" />
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

// ── chunk card (decomposition view) ─────────────────────────

export function ChunkCard({ chunk, active, elapsedMs }: { chunk: WorkChunk; active?: boolean; elapsedMs?: number }) {
  const ring = active ? "ring-1 ring-accent shadow-glow" : "";
  const metCount = chunk.acceptance.filter((a) => a.met === true).length;
  const totalCount = chunk.acceptance.length;
  const allDone = totalCount > 0 && metCount === totalCount;
  const tone = STATUS_TONE[chunk.status].tone;
  const glyph = STATUS_TONE[chunk.status].glyph;

  // The numbered circle IS the status atom. Color, glyph, and pulse all
  // derive from chunk.status — no redundant DONE/RUNNING pill needed.
  const circleBase = "shrink-0 w-9 h-9 rounded-full grid place-items-center text-sm font-semibold relative";
  const circleByStatus =
    chunk.status === "completed"   ? "bg-ok/20 text-ok ring-1 ring-ok/40"
  : chunk.status === "in_progress" ? "bg-accent/20 text-accent-soft ring-1 ring-accent/40 animate-pulse-ring"
  : chunk.status === "blocked"     ? "bg-danger/20 text-danger-soft ring-1 ring-danger/40"
  : chunk.status === "skipped"     ? "bg-ink-800 text-fg-dim line-through"
  :                                  "bg-ink-800 text-fg-mute ring-1 ring-ink-700";

  return (
    <div className={`rounded-xl border border-ink-700 bg-ink-900/40 p-4 ${ring} transition-all`}>
      <div className="flex items-start gap-3">
        <div className={`${circleBase} ${circleByStatus}`} aria-label={`chunk ${chunk.n} ${STATUS_TONE[chunk.status].label}`}>
          {chunk.status === "completed" || chunk.status === "blocked"
            ? <span aria-hidden>{glyph}</span>
            : <span className="font-mono">{chunk.n}</span>}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-3 flex-wrap">
            <div className="font-semibold text-fg leading-tight">{chunk.title}</div>
            <span className="text-[10px] uppercase tracking-[0.08em] text-fg-mute font-mono">
              #{chunk.n}
            </span>
            <div className="flex-1" />
            {chunk.attempts > 1 && (
              <Chip tone="warn" title={`retried ${chunk.attempts - 1}× before completing`}>
                ↻ retry {chunk.attempts - 1}
              </Chip>
            )}
            {elapsedMs != null && elapsedMs > 0 && (
              <span className="text-[10px] text-fg-mute font-mono tabular-nums" title="elapsed time">
                {formatDuration(elapsedMs)}
              </span>
            )}
          </div>
          <div className="text-sm text-fg-mute mt-1 leading-relaxed">{chunk.description}</div>
          {chunk.file_hints.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {chunk.file_hints.map((f, i) => (
                <code key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ink-800/60 text-fg-dim border border-ink-700">{f}</code>
              ))}
            </div>
          )}
          {chunk.acceptance.length > 0 && (
            <div className="mt-3 pl-3 border-l border-ink-700 space-y-0.5">
              {/* No "ACCEPTANCE (n/n)" label — the rows speak for themselves;
                  the count lives in the segmented bar above the chunk list. */}
              {chunk.acceptance.map((ac) => (
                <CriterionRow key={ac.id} ac={ac} hideVerification={allDone && ac.met === true} />
              ))}
            </div>
          )}
          {chunk.last_error && chunk.status === "blocked" && (
            <div className="mt-2 text-xs text-danger-soft border border-danger/30 bg-danger/5 rounded-lg p-2">
              <span className="font-mono mr-1.5" aria-hidden>⊘</span>
              {chunk.last_error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = s / 60;
  if (m < 60) return `${m.toFixed(m < 10 ? 1 : 0)}m`;
  const h = m / 60;
  return `${h.toFixed(1)}h`;
}

// ── empty state ─────────────────────────────────────────────

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="text-center text-fg-mute py-16 px-6">
      {icon && <div className="text-3xl mb-3 text-fg-dim">{icon}</div>}
      <div className="text-lg text-fg mb-1">{title}</div>
      {hint && <div className="text-sm text-fg-dim mb-4">{hint}</div>}
      {action}
    </div>
  );
}

// ── chip ────────────────────────────────────────────────────

export function Chip({ children, tone = "default", title }: { children: ReactNode; tone?: "default" | "accent" | "ok" | "warn" | "danger"; title?: string }) {
  const cls = tone === "accent" ? TONE_BG.accent : tone === "ok" ? TONE_BG.ok : tone === "warn" ? TONE_BG.warn : tone === "danger" ? TONE_BG.danger : "bg-ink-800 text-fg-mute";
  return (
    <span title={title} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] uppercase tracking-wider border border-transparent ${cls}`}>
      {children}
    </span>
  );
}
