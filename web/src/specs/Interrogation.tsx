/* Interrogation panel — the multi-turn Q&A that locks the spec down.
 *
 * Each question gets a kind-specific input:
 * - binary → yes/no segmented control
 * - choice → radio chips
 * - value  → text input
 *
 * Skip is allowed; the user can also force /ship at any time. Importance
 * pips and "why" reveal communicate weight without nagging the user.
 */
import { useEffect, useState } from "react";
import type { ClarifyingQuestion } from "../api";
import { ImportancePips, Chip } from "./components";

export function InterrogationPanel({
  questions,
  rounds,
  maxRounds,
  busy,
  onSubmit,
  onShip,
}: {
  questions: ClarifyingQuestion[];
  rounds: number;
  maxRounds: number;
  busy: boolean;
  onSubmit: (answers: { n: number; answer: string }[]) => void;
  onShip: () => void;
}) {
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [showWhy, setShowWhy] = useState<Record<number, boolean>>({});
  // reset drafts when a fresh question set arrives
  useEffect(() => { setDraft({}); }, [questions.map((q) => q.n).join(",")]);

  const answered = questions.filter((q) => (draft[q.n] ?? "").trim().length > 0);

  return (
    <div className="space-y-3 animate-slide-up">
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-xl font-semibold tracking-tight">Clarifying questions</h3>
        <span className="text-[10px] uppercase tracking-[0.12em] text-fg-mute font-mono">
          round {rounds + 1} / {maxRounds}
        </span>
        <div className="flex-1" />
        <button
          onClick={onShip}
          className="px-3 py-1.5 rounded-lg text-xs font-mono border border-ink-700 hover:border-accent/50 hover:bg-ink-800 text-fg-mute hover:text-fg transition"
          title="skip remaining questions and treat the spec as ready"
        >
          /ship
        </button>
      </div>

      <div className="space-y-3">
        {questions.map((q) => (
          <div key={q.n} className="rounded-xl border border-ink-700 bg-ink-900/40 p-4">
            <div className="flex items-start gap-3">
              <span className="shrink-0 mt-0.5 w-8 h-8 rounded-full bg-ink-800 text-fg-mute grid place-items-center text-xs font-mono ring-1 ring-ink-700">Q{q.n}</span>
              <div className="flex-1 min-w-0 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <ImportancePips value={q.importance} />
                  <Chip>{q.kind}</Chip>
                </div>
                <div className="text-fg leading-relaxed">{q.text}</div>
                {q.why && (
                  <div>
                    <button
                      onClick={() => setShowWhy((s) => ({ ...s, [q.n]: !s[q.n] }))}
                      className="text-[11px] text-fg-mute hover:text-fg flex items-center gap-1 transition"
                    >
                      <span aria-hidden>{showWhy[q.n] ? "▾" : "▸"}</span>
                      {showWhy[q.n] ? "hide rationale" : "why this matters"}
                    </button>
                    {showWhy[q.n] && (
                      <div className="text-xs text-fg-mute italic mt-1 pl-3 border-l border-ink-700">{q.why}</div>
                    )}
                  </div>
                )}
                <QuestionInput
                  question={q}
                  value={draft[q.n] ?? ""}
                  onChange={(v) => setDraft((d) => ({ ...d, [q.n]: v }))}
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3">
        <span className="text-xs text-fg-mute flex-1">
          <span className="font-mono tabular-nums text-fg">{answered.length}</span>
          <span className="text-fg-mute">/{questions.length} answered</span>
          <span className="text-fg-dim"> · skipped questions stay open for a future round</span>
        </span>
        <button
          onClick={() => onSubmit(answered.map((q) => ({ n: q.n, answer: draft[q.n].trim() })))}
          disabled={busy || answered.length === 0}
          className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft transition shadow-glow text-white text-sm font-medium"
        >
          {busy ? "integrating…" : `submit ${answered.length} answer${answered.length === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}

function QuestionInput({ question, value, onChange }: { question: ClarifyingQuestion; value: string; onChange: (v: string) => void }) {
  if (question.kind === "binary") {
    return (
      <div className="flex gap-2">
        {["yes", "no"].map((opt) => (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className={`px-4 py-1.5 rounded-lg border text-sm transition ${
              value === opt
                ? "bg-accent text-white border-accent shadow-glow"
                : "border-ink-700 hover:border-accent/40 text-fg-mute"
            }`}
          >
            {opt}
          </button>
        ))}
      </div>
    );
  }
  if (question.kind === "choice" && question.choices.length > 0) {
    return (
      <div className="flex gap-2 flex-wrap">
        {question.choices.map((c) => (
          <button
            key={c}
            onClick={() => onChange(c)}
            className={`px-3 py-1.5 rounded-lg border text-sm transition ${
              value === c
                ? "bg-accent/20 border-accent text-accent-soft"
                : "border-ink-700 text-fg-mute hover:border-accent/40"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
    );
  }
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="your answer…"
      className="w-full bg-ink-900 border border-ink-700 focus:border-accent focus:outline-none rounded-lg px-3 py-2 text-sm"
    />
  );
}
