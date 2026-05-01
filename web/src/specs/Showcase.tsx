/* Showcase — renders every SpecPanel state with realistic mock data.
 *
 * Used by `?showcase=spec` for visual regression / design review. Each
 * section is labeled so screenshots are self-documenting. Mock data is
 * structured to exercise edge cases: blocked chunks, retries, partial
 * verification, multi-round interrogation, varied criterion verification.
 */
import { useState } from "react";
import type {
  AcceptanceCriterion, ClarifyingQuestion, Spec, SpecRow, WorkChunk,
} from "../api";
import { THEMES, applyTheme, type ThemeId } from "../theme";
import { Card, ChunkCard, EmptyState, SegmentedProgress, StatusBadge, Chip, CriterionRow, ImportancePips } from "./components";
import { PhaseTimeline } from "./PhaseTimeline";
import { InterrogationPanel } from "./Interrogation";
import { ExecutionStream, type SpecEvent } from "./ExecutionStream";

// ── mock factories ──────────────────────────────────────────

function ac(id: string, text: string, verification: string, met: boolean | null = null, evidence = ""): AcceptanceCriterion {
  return { id, text, verification, met, evidence };
}

function chunk(n: number, title: string, description: string, status: WorkChunk["status"], opts: Partial<WorkChunk> = {}): WorkChunk {
  return {
    n, title, description, status,
    file_hints: opts.file_hints ?? [],
    acceptance: opts.acceptance ?? [],
    notes: opts.notes ?? "",
    attempts: opts.attempts ?? 1,
    last_error: opts.last_error ?? "",
  };
}

function makeBaseSpec(): Spec {
  return {
    id: "dark-mode-toggle-a3f29c",
    title: "Dark Mode Toggle",
    goal: "Add a dark mode toggle to settings; persists across reloads via localStorage; uses CSS variables only",
    summary: "Add a user-toggleable dark mode that flips between light and dark themes. The toggle lives in the Settings page and persists across page reloads via localStorage. Implementation uses CSS custom properties — no duplicated stylesheets.",
    requirements: [
      "Toggle visible and accessible from the Settings page",
      "Theme preference persists across page reloads",
      "Color scheme uses CSS custom properties (`--color-*`) on `:root`",
      "Default state respects the user's last choice; falls back to dark",
    ],
    constraints: [
      "Use existing Tailwind setup — no new CSS-in-JS library",
      "No flicker on initial paint (apply theme before React hydrates)",
    ],
    out_of_scope: [
      "System-preference (`prefers-color-scheme`) auto-detection",
      "Per-page theme overrides",
    ],
    open_questions: [],
    work_chunks: [],
    global_acceptance: [
      ac("global-ac1", "Theme persists across hard reload", "shell_exec: npm test -- theme.persist"),
      ac("global-ac2", "No FOUC on initial paint", "grep 'data-theme' index.html"),
    ],
    history: [
      "draft: 4 requirements",
      "Q1: Where does the toggle live? → settings page",
      "Q2: Should it persist? → yes",
    ],
    readiness: { score: 5, ready: true, blockers: [], summary: "ironclad — every requirement is testable" },
    verification: null,
    status: "ready",
    created_at: Date.now() / 1000 - 3600,
    updated_at: Date.now() / 1000,
    rounds: 2,
  };
}

function makeQuestions(): ClarifyingQuestion[] {
  return [
    {
      n: 1, text: "Should the toggle persist across page reloads?",
      why: "Persistence shapes whether we use localStorage or just session state.",
      importance: 5, kind: "binary", choices: [], answer: null,
    },
    {
      n: 2, text: "Where in the UI should the toggle live?",
      why: "Different placements imply different component-tree changes.",
      importance: 4, kind: "choice",
      choices: ["Settings page", "Header navigation", "Both"], answer: null,
    },
    {
      n: 3, text: "What CSS variable naming convention should the theme tokens follow?",
      why: "We'll reference these in dozens of components — naming sticks.",
      importance: 3, kind: "value", choices: [], answer: null,
    },
  ];
}

function makeChunks(): WorkChunk[] {
  return [
    chunk(1, "Define theme CSS variables", "Add `--color-bg`, `--color-fg`, `--color-accent` and friends in :root with light/dark variants under `[data-theme]` selectors.",
      "completed", {
        file_hints: ["src/index.css", "tailwind.config.js"],
        acceptance: [
          ac("c1-ac1", "--color-bg defined for both themes", "grep -nE -- '--color-bg' src/index.css", true,
             "Found at line 8 (:root) and line 25 ([data-theme=light])"),
          ac("c1-ac2", "Tailwind config references CSS vars", "grep 'var(--' tailwind.config.js", true,
             "5 occurrences confirm var-driven palette"),
        ],
        notes: "Added 18 tokens across two theme blocks; existing components re-themed with no edits.",
      }),
    chunk(2, "Wire ThemeProvider + persistence", "Build a React hook that reads localStorage and applies `data-theme` to `<html>`. Default to dark.",
      "completed", {
        file_hints: ["src/theme.ts", "src/main.tsx"],
        acceptance: [
          ac("c2-ac1", "Hook persists choice to localStorage", "grep -n 'localStorage' src/theme.ts", true,
             "setItem on every change, getItem on mount"),
        ],
        notes: "useTheme hook + applyTheme helper; no flicker observed in dev.",
      }),
    chunk(3, "Build ToggleButton component", "Settings-page toggle switch that calls into the theme hook. Includes ARIA labels.",
      "in_progress", {
        file_hints: ["src/components/ThemeToggle.tsx", "src/Settings.tsx"],
        acceptance: [
          ac("c3-ac1", "ToggleButton rendered in Settings", "glob src/Settings.tsx && grep ThemeToggle src/Settings.tsx", null),
          ac("c3-ac2", "Component has ARIA role=switch", "grep -n 'role=\"switch\"' src/components/ThemeToggle.tsx", null),
        ],
      }),
    chunk(4, "Add no-flicker init script", "Inject a small script in index.html that reads localStorage and sets data-theme BEFORE React hydrates.",
      "pending", {
        file_hints: ["index.html"],
        acceptance: [
          ac("c4-ac1", "Inline script reads localStorage", "grep -n 'localStorage' index.html", null),
          ac("c4-ac2", "Script sets data-theme before body renders", "grep -nB1 'data-theme' index.html", null),
        ],
      }),
    chunk(5, "Write theme persistence test", "End-to-end test that toggles, reloads, and asserts theme survives.",
      "blocked", {
        file_hints: ["tests/theme.spec.ts"],
        acceptance: [
          ac("c5-ac1", "Test file exists", "glob tests/theme.spec.ts", false, "no matches"),
          ac("c5-ac2", "Test asserts post-reload theme", "grep 'reload' tests/theme.spec.ts", false, "file not found"),
        ],
        attempts: 2,
        last_error: "test runner not configured for browser environment; missing playwright dependency",
      }),
  ];
}

function makeRunningEvents(chunks: WorkChunk[]): SpecEvent[] {
  return [
    // chunk 1 — quick path
    { type: "plan", chunk_n: 1, plan: { goal: "Define theme variables", steps: [
      { n: 1, description: "Read existing index.css to see structure", tool: "read", arguments: { path: "src/index.css" }},
      { n: 2, description: "Define color tokens for both themes", tool: null, arguments: {} },
      { n: 3, description: "Verify variables compile via tailwind build", tool: "shell_exec", arguments: { command: "npx tailwindcss --check" }},
    ]}},
    { type: "step_start", chunk_n: 1, step: { n: 1, description: "Read existing index.css to see structure", tool: "read" }},
    { type: "step", chunk_n: 1, result: { step: { n: 1, description: "Read", tool: "read" }, tool_result: { ok: true, output: "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n\n:root {\n  /* existing vars */\n}\n", meta: { cached: false }}}},
    { type: "step", chunk_n: 1, result: { step: { n: 2, description: "edit", tool: "edit" }, tool_result: { ok: true, output: "+18 lines, -2 lines", meta: { cached: false }}}},
    { type: "step", chunk_n: 1, result: { step: { n: 3, description: "build", tool: "shell_exec" }, tool_result: { ok: true, output: "✓ tailwind compiled in 240ms\n✓ no warnings", meta: {}}}},
    { type: "done_check", chunk_n: 1, done_check: { overall: "complete", criteria: [
      { description: "Variables defined for :root and [data-theme]", met: true, evidence: "step 2 wrote 18 lines" },
      { description: "Tailwind compiles without warnings", met: true, evidence: "step 3 succeeded" },
    ], gaps: []}},
    { type: "criterion_verified", chunk_n: 1, criterion: { id: "c1-ac1", text: "--color-bg defined for both themes",
      verification: "grep -nE -- '--color-bg' src/index.css",
      met: true, evidence: "Found at line 8 (:root) and line 25 ([data-theme=light])" }},
    { type: "criterion_verified", chunk_n: 1, criterion: { id: "c1-ac2", text: "Tailwind config references CSS vars",
      verification: "grep 'var(--' tailwind.config.js",
      met: true, evidence: "5 occurrences confirm var-driven palette" }},

    // chunk 3 — currently running
    { type: "plan", chunk_n: 3, plan: { goal: "Build ToggleButton", steps: [
      { n: 1, description: "Glob existing Settings to confirm location", tool: "glob", arguments: { pattern: "src/Settings.tsx" }},
      { n: 2, description: "Create ThemeToggle component with ARIA switch", tool: "file_write", arguments: { path: "src/components/ThemeToggle.tsx" }},
      { n: 3, description: "Mount it in Settings page", tool: "edit", arguments: {}},
      { n: 4, description: "Verify rendering with grep", tool: "grep", arguments: { pattern: "ThemeToggle", path: "src/Settings.tsx" }},
    ]}},
    { type: "critique", chunk_n: 3, critique: { issues: [
      { severity: "low", step: 2, concern: "consider extracting label into i18n strings", suggestion: "out of scope for this chunk; defer" }
    ], verdict: "ship", summary: "plan looks solid"}},
    { type: "step_start", chunk_n: 3, step: { n: 1, description: "Glob existing Settings to confirm location", tool: "glob" }},
    { type: "step", chunk_n: 3, result: { step: { n: 1, description: "Glob", tool: "glob" }, tool_result: { ok: true, output: "src/Settings.tsx", meta: { cached: true }}}},
    { type: "step_start", chunk_n: 3, step: { n: 2, description: "Create ThemeToggle component", tool: "file_write" }},

    // chunk 5 — blocked, retried
    { type: "chunk_retry", chunk_n: 5, failed: [{ id: "c5-ac1", text: "Test file exists" }] },
    { type: "warning", chunk_n: 5, stage: "executor", error: "shell_exec: npx playwright install timed out after 30s" },
  ];
}

// ── showcase ────────────────────────────────────────────────

export function Showcase() {
  const [theme, setTheme] = useState<ThemeId>(() => "midnight");
  function setT(t: ThemeId) { setTheme(t); applyTheme(t); }

  const baseSpec = makeBaseSpec();
  const interrogateSpec: Spec = { ...baseSpec, status: "draft", rounds: 0,
    open_questions: makeQuestions(), readiness: null, work_chunks: [] };
  const draftSpec: Spec = { ...baseSpec, status: "draft", rounds: 0, open_questions: [],
    readiness: null, work_chunks: [], summary: "Add a user-toggleable dark mode."};
  const decomposeSpec: Spec = { ...baseSpec, status: "ready", rounds: 2, work_chunks: [] };
  const executingSpec: Spec = { ...baseSpec, status: "executing", work_chunks: makeChunks() };
  const verifiedChunks = makeChunks().map((c) => ({
    ...c, status: "completed" as const,
    acceptance: c.acceptance.map((a) => ({ ...a, met: true as const, evidence: a.evidence || "verified" })),
  }));
  const verifiedSpec: Spec = { ...baseSpec, status: "verified", work_chunks: verifiedChunks,
    global_acceptance: baseSpec.global_acceptance.map((a) => ({ ...a, met: true, evidence: "passed" })),
    verification: { overall: "verified", chunks_completed: 5, chunks_total: 5, criteria_met: 12, criteria_total: 12, gaps: [] }};
  const partialSpec: Spec = { ...baseSpec, status: "partial", work_chunks: makeChunks(),
    verification: { overall: "partial", chunks_completed: 3, chunks_total: 5, criteria_met: 7, criteria_total: 12,
      gaps: ["chunk 5 (Write theme persistence test) blocked: playwright not installed",
             "chunk 4 (Add no-flicker init script): never started — chunk 5 was an upstream blocker"]}};

  const events = makeRunningEvents(makeChunks());

  const sampleRows: SpecRow[] = [
    { id: "dark-mode-toggle-a3f29c", title: "Dark Mode Toggle", status: "verified", updated_at: Date.now() / 1000 - 60, chunks: 5, rounds: 2 },
    { id: "auth-rewrite-7b1e44",    title: "Replace auth middleware",            status: "executing", updated_at: Date.now() / 1000 - 600, chunks: 8, rounds: 3 },
    { id: "rag-folder-ingest-deef00", title: "RAG: folder ingestion",            status: "ready",    updated_at: Date.now() / 1000 - 3600, chunks: 4, rounds: 1 },
    { id: "memory-dedup-1234ab",     title: "Memory dedup tuning",               status: "draft",    updated_at: Date.now() / 1000 - 7200, chunks: 0, rounds: 0 },
    { id: "settings-i18n-99cc11",    title: "i18n the settings panel",           status: "partial",  updated_at: Date.now() / 1000 - 86400, chunks: 6, rounds: 2 },
    { id: "agent-tool-cache-1f0099", title: "Tool result cache layer",           status: "failed",   updated_at: Date.now() / 1000 - 172800, chunks: 3, rounds: 2 },
  ];

  return (
    <div className="min-h-screen">
      {/* Floating theme picker — each pill carries a swatch dot of its accent
          so the user previews the brand color before clicking. */}
      <div className="fixed top-3 right-3 z-50 bg-ink-900/80 backdrop-blur border border-ink-700 rounded-xl p-1.5 flex gap-1 shadow-glow">
        {THEMES.map((t) => {
          const active = theme === t.id;
          return (
            <button key={t.id} onClick={() => setT(t.id)}
              className={`px-2.5 py-1 rounded-lg text-xs flex items-center gap-1.5 transition ${
                active
                  ? "bg-ink-800 ring-1 ring-accent/50 text-fg"
                  : "text-fg-mute hover:bg-ink-800 hover:text-fg"
              }`}>
              <span className="w-2 h-2 rounded-full" style={{ background: t.swatch[1] }} />
              {t.name}
            </button>
          );
        })}
      </div>

      <div className="max-w-6xl mx-auto px-6 py-10 space-y-12">
        <header className="space-y-2 pb-4 border-b border-ink-700">
          <div className="text-[10px] uppercase tracking-[0.18em] text-fg-mute font-mono">Design review · spec-driven mode</div>
          <h1 className="text-5xl font-semibold tracking-tight leading-none">
            <span className="text-accent">Local</span>Agent <span className="text-fg-mute font-light">showcase</span>
          </h1>
          <p className="text-fg-mute mt-3">Active theme: <span className="text-fg font-medium">{theme}</span> · every panel rendered with realistic data for visual review.</p>
        </header>

        <Section label="01 · Status atoms">
          <div className="flex items-center gap-3 flex-wrap">
            <StatusBadge status="draft" /><StatusBadge status="ready" /><StatusBadge status="executing" pulse />
            <StatusBadge status="verified" /><StatusBadge status="partial" /><StatusBadge status="failed" />
            <StatusBadge status="blocked" /><StatusBadge status="completed" />
          </div>
          <div className="flex items-center gap-4 flex-wrap mt-3">
            <span className="text-xs text-fg-dim">importance:</span>
            {[1, 2, 3, 4, 5].map((n) => <ImportancePips key={n} value={n} />)}
            <span className="text-xs text-fg-dim ml-4">chips:</span>
            <Chip>cached</Chip><Chip tone="ok">verified</Chip><Chip tone="warn">retry 2</Chip><Chip tone="danger">blocked</Chip>
          </div>
        </Section>

        <Section label="02 · Spec list (sidebar items)">
          <div className="grid gap-2 max-w-md">
            {sampleRows.map((r) => {
              const ago = Math.floor(Date.now() / 1000 - r.updated_at);
              const rel = ago < 60 ? `${ago}s ago`
                : ago < 3600 ? `${Math.floor(ago / 60)}m ago`
                : ago < 86400 ? `${Math.floor(ago / 3600)}h ago`
                : `${Math.floor(ago / 86400)}d ago`;
              return (
                <div key={r.id} className="rounded-lg bg-ink-900/40 border border-ink-700 px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-fg truncate font-medium">{r.title}</div>
                      <div className="text-[11px] text-fg-mute flex gap-2 mt-0.5 items-center">
                        <span className="tabular-nums">{r.chunks} chunk{r.chunks === 1 ? "" : "s"}</span>
                        <span className="text-fg-dim">·</span>
                        <span className="text-fg-dim tabular-nums">{rel}</span>
                      </div>
                    </div>
                    <StatusBadge status={r.status} />
                  </div>
                </div>
              );
            })}
          </div>
        </Section>

        <Section label="03 · Phase: draft">
          <PhaseTimeline spec={draftSpec} />
          <div className="mt-4">
            <Card title="summary" tone="accent">
              <p className="text-sm leading-relaxed">{draftSpec.summary}</p>
            </Card>
          </div>
        </Section>

        <Section label="04 · Phase: interrogate">
          <PhaseTimeline spec={interrogateSpec} />
          <div className="mt-4">
            <InterrogationPanel
              questions={makeQuestions()}
              rounds={0}
              maxRounds={3}
              busy={false}
              onSubmit={() => {}}
              onShip={() => {}}
            />
          </div>
        </Section>

        <Section label="05 · Phase: decompose ready (no chunks yet)">
          <PhaseTimeline spec={decomposeSpec} />
          <div className="mt-4">
            <Card tone="info" title="ready · decompose into chunks">
              <div className="text-sm text-fg-mute mb-3">
                The spec looks ironclad. The agent will now split it into bite-sized chunks, each with explicit acceptance criteria.
              </div>
              <button className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft text-white shadow-glow text-sm">decompose</button>
            </Card>
          </div>
        </Section>

        <Section label="06 · Phase: executing (mid-run)">
          <PhaseTimeline spec={executingSpec} />
          <div className="mt-4 space-y-4">
            <Card tone="default" title="chunks">
              <div className="flex items-baseline gap-3 mb-2">
                <div className="text-2xl font-semibold tabular-nums leading-none">
                  <span className="text-ok">2</span>
                  <span className="text-fg-dim mx-0.5">/</span>
                  <span className="text-fg-mute">5</span>
                </div>
                <span className="text-[10px] uppercase tracking-[0.12em] text-fg-mute">complete</span>
                <span className="text-xs text-danger-soft flex items-center gap-1"><span aria-hidden>⊘</span> 1 blocked</span>
              </div>
              <SegmentedProgress statuses={executingSpec.work_chunks.map((c) => c.status)} pulseRunning />
              <div className="grid gap-3 mt-5">
                {executingSpec.work_chunks.map((c, i) => {
                  // Mock elapsed times: completed chunks have a duration; running chunk has elapsed.
                  const elapsed = c.status === "completed" ? [4200, 8800, 0, 0, 0][i]
                                : c.status === "in_progress" ? 18400
                                : c.status === "blocked" ? 32100
                                : 0;
                  return <ChunkCard key={c.n} chunk={c} active={c.status === "in_progress"} elapsedMs={elapsed} />;
                })}
              </div>
            </Card>
            <ExecutionStream events={events} chunks={executingSpec.work_chunks} running />
          </div>
        </Section>

        <Section label="07 · Phase: verified">
          <PhaseTimeline spec={verifiedSpec} />
          <div className="mt-4">
            <ExecutionStream
              events={[{ type: "spec_verified", verification: verifiedSpec.verification }]}
              chunks={verifiedSpec.work_chunks}
              running={false}
            />
          </div>
        </Section>

        <Section label="08 · Phase: partial verification">
          <PhaseTimeline spec={partialSpec} />
          <div className="mt-4">
            <ExecutionStream
              events={[{ type: "spec_verified", verification: partialSpec.verification }]}
              chunks={partialSpec.work_chunks}
              running={false}
            />
          </div>
        </Section>

        <Section label="09 · Empty state">
          <EmptyState
            icon="✦"
            title="no spec selected"
            hint="pick a spec from the left, or start a new one."
            action={
              <button className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft text-white shadow-glow">
                + new spec
              </button>
            }
          />
        </Section>

        <Section label="10 · Acceptance criterion variants">
          <div className="rounded-xl border border-ink-700 bg-ink-800/40 p-4 max-w-3xl">
            <CriterionRow ac={ac("v1", "color tokens defined", "grep '--color-' src/index.css", true, "found 18 tokens")} />
            <CriterionRow ac={ac("v2", "Settings rendered toggle", "grep 'ThemeToggle' src/Settings.tsx", false, "no match — toggle missing or path wrong")} />
            <CriterionRow ac={ac("v3", "tests pass", "shell_exec: npm test", null, "")} />
          </div>
        </Section>
      </div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  // Section labels: monospace ordinal + uppercase title at consistent tracking,
  // with the accent rule as the sole decorative element. Same scale, same tracking
  // across all sections so the eye anchors on content, not labels.
  const [ordinal, ...rest] = label.split("·").map((s) => s.trim());
  return (
    <section className="space-y-3 scroll-mt-12">
      <div className="flex items-center gap-3 border-l-2 border-accent pl-3">
        <span className="text-[11px] font-mono text-fg-dim tabular-nums">{ordinal}</span>
        <h2 className="text-[11px] uppercase tracking-[0.12em] text-fg-mute font-medium">{rest.join(" · ")}</h2>
      </div>
      {children}
    </section>
  );
}
