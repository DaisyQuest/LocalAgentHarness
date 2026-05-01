/* PhaseTimeline — sticky breadcrumb showing where the spec is in its lifecycle.
 *
 * Phases: draft → interrogate → decompose → execute → verify
 * Visually a horizontal stepper with completed / active / upcoming states.
 * Stays sticky at the top of the runner so the user always has the map.
 */
import type { Spec } from "../api";

type Phase = "draft" | "interrogate" | "decompose" | "execute" | "verify";

const PHASES: { id: Phase; label: string; hint: string }[] = [
  { id: "draft",       label: "Draft",       hint: "agent reads your goal and structures it" },
  { id: "interrogate", label: "Interrogate", hint: "ranked clarifying questions until ironclad" },
  { id: "decompose",   label: "Decompose",   hint: "split into bite-sized chunks with acceptance" },
  { id: "execute",     label: "Execute",     hint: "run each chunk; verify against the workspace" },
  { id: "verify",      label: "Verify",      hint: "global acceptance check; final scorecard" },
];

function currentPhase(spec: Spec): Phase {
  if (spec.status === "verified" || spec.status === "partial" || spec.status === "failed") return "verify";
  if (spec.status === "executing") return "execute";
  if (spec.status === "ready" && spec.work_chunks.length > 0) return "execute";
  if (spec.status === "ready") return "decompose";
  if (spec.rounds > 0 || spec.open_questions.length > 0) return "interrogate";
  return "draft";
}

function phaseDone(phase: Phase, spec: Spec): boolean {
  const order: Phase[] = ["draft", "interrogate", "decompose", "execute", "verify"];
  const cur = currentPhase(spec);
  return order.indexOf(phase) < order.indexOf(cur);
}

export function PhaseTimeline({ spec }: { spec: Spec }) {
  const cur = currentPhase(spec);
  return (
    <div className="sticky top-0 z-20 bg-ink-900/85 backdrop-blur border-b border-ink-700">
      <div className="px-6 py-3">
        <div className="flex items-center gap-2 text-xs">
          {PHASES.map((p, i) => {
            const isActive = p.id === cur;
            const isDone = phaseDone(p.id, spec);
            // The active step is the LAST one with a solid connector behind it;
            // future connectors are dashed so the eye reads "reachable from here"
            // rather than "already unlocked". Inactive numerals tabular-nums.
            const dotCls = isDone
              ? "bg-ok text-ink-950 ring-1 ring-ok/40"
              : isActive
              ? "bg-accent text-ink-950 ring-2 ring-accent/30 animate-pulse-ring"
              : "bg-ink-800 text-fg-dim ring-1 ring-ink-700";
            const labelCls = isActive ? "text-fg font-semibold" : isDone ? "text-fg-mute" : "text-fg-dim";
            const nextDone = i < PHASES.length - 1 ? phaseDone(PHASES[i + 1].id, spec) : false;
            const nextActive = i < PHASES.length - 1 ? PHASES[i + 1].id === cur : false;
            const connectorSolid = isDone && (nextDone || nextActive);
            return (
              <div key={p.id} className="flex items-center gap-2 min-w-0" title={p.hint}>
                <span className={`shrink-0 w-5 h-5 rounded-full grid place-items-center text-[10px] font-bold tabular-nums ${dotCls}`}>
                  {isDone ? "✓" : i + 1}
                </span>
                <span className={`uppercase tracking-[0.1em] text-[11px] ${labelCls}`}>{p.label}</span>
                {i < PHASES.length - 1 && (
                  connectorSolid
                    ? <span className="shrink-0 w-6 sm:w-10 md:w-16 h-px bg-ok/60" />
                    : <span className="shrink-0 w-6 sm:w-10 md:w-16 h-px border-t border-dashed border-ink-700" style={{ background: "transparent" }} />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
