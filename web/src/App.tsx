import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "./api";
import { ThemeSwitcher, useTheme } from "./ThemeSwitcher";
import { SpecPanel } from "./specs/SpecPanel";
import { Showcase } from "./specs/Showcase";

type Mode = "chat" | "agent";
type Panel = "chat" | "spec" | "rag" | "memory" | "strategy" | "settings";

// Visual regression / design-review entry point: ?showcase=spec renders
// every SpecPanel state with mock data. Bypasses the chat layout entirely.
function isShowcase(): boolean {
  if (typeof window === "undefined") return false;
  const sp = new URLSearchParams(window.location.search);
  return sp.get("showcase") === "spec";
}

type AgentEvent =
  | { type: "reframe"; reframe: any }
  | { type: "clarification_needed"; ambiguity: number; question: string; assumptions: string[] }
  | { type: "plan"; plan: any }
  | { type: "critique"; critique: any }
  | { type: "plan_revised"; plan: any }
  | { type: "step_start"; step: any }
  | { type: "step"; result: any }
  | { type: "done_check"; done_check: any }
  | { type: "token"; delta: string }
  | { type: "done"; answer: string }
  | { type: "warning"; stage: string; error: string }
  | { type: "error"; error: string };

type ChatItem =
  | { kind: "user"; content: string }
  | { kind: "assistant"; content: string; meta?: api.ChatMeta }
  | { kind: "agent"; events: AgentEvent[]; goal: string };

export default function App() {
  const [theme, setTheme] = useTheme();
  if (isShowcase()) return <Showcase />;
  return <MainApp theme={theme} setTheme={setTheme} />;
}

function MainApp({ theme, setTheme }: { theme: any; setTheme: any }) {
  const [conversations, setConversations] = useState<api.Conversation[]>([]);
  const [cid, setCid] = useState<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [role, setRole] = useState("auto");
  const [useRag, setUseRag] = useState(false);
  const [useMemory, setUseMemory] = useState(true);
  const [mode, setMode] = useState<Mode>("chat");
  const [panel, setPanel] = useState<Panel>("chat");
  const [busy, setBusy] = useState(false);
  const [verbose, setVerbose] = useState(true);
  const [autoApprove, setAutoApprove] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.listConversations().then(setConversations).catch(() => {}); }, []);
  useEffect(() => { scrollRef.current?.scrollTo({ top: 9e9, behavior: "smooth" }); }, [items, busy]);

  async function loadConv(id: string) {
    setCid(id);
    const msgs = await api.loadMessages(id);
    setItems(msgs.filter((m) => m.role === "user" || m.role === "assistant").map((m) =>
      m.role === "user" ? { kind: "user", content: m.content } : { kind: "assistant", content: m.content }
    ));
    setPanel("chat");
  }

  async function newChat() {
    const id = await api.newConversation();
    setCid(id);
    setItems([]);
    setConversations(await api.listConversations());
    setPanel("chat");
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    try {
      if (mode === "chat") {
        setItems((it) => [...it, { kind: "user", content: text }, { kind: "assistant", content: "" }]);
        for await (const ev of api.streamChat({ conversation_id: cid, message: text, role, use_rag: useRag, use_memory: useMemory })) {
          if (ev.meta) {
            if (ev.meta.cid && !cid) setCid(ev.meta.cid);
            setItems((it) => {
              const copy = [...it];
              const last = copy[copy.length - 1] as Extract<ChatItem, { kind: "assistant" }>;
              copy[copy.length - 1] = { ...last, meta: ev.meta };
              return copy;
            });
          }
          if (ev.delta) setItems((it) => {
            const copy = [...it];
            const last = copy[copy.length - 1] as Extract<ChatItem, { kind: "assistant" }>;
            copy[copy.length - 1] = { ...last, content: last.content + ev.delta };
            return copy;
          });
        }
      } else {
        setItems((it) => [...it, { kind: "user", content: text }, { kind: "agent", events: [], goal: text }]);
        for await (const ev of api.runAgent(text, autoApprove)) {
          setItems((it) => {
            const copy = [...it];
            const last = copy[copy.length - 1] as Extract<ChatItem, { kind: "agent" }>;
            copy[copy.length - 1] = { ...last, events: [...last.events, ev] };
            return copy;
          });
        }
      }
      setConversations(await api.listConversations());
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full flex">
      <Sidebar
        conversations={conversations} cid={cid} onLoad={loadConv} onNew={newChat}
        onDelete={async (id: string) => {
          await api.deleteConversation(id);
          if (cid === id) { setCid(null); setItems([]); }
          setConversations(await api.listConversations());
        }}
        panel={panel} onPanel={setPanel}
      />
      <main className="flex-1 flex flex-col">
        <header className="border-b border-ink-700 px-6 py-3 flex items-center gap-3 bg-ink-900/40 backdrop-blur">
          <div className="font-semibold tracking-tight text-lg">
            <span className="text-accent">Local</span>Agent
          </div>
          <span className="text-xs text-fg-dim">v0.3</span>
          <span className="text-xs text-fg-dim ml-2 capitalize">· {panel}</span>
          <div className="flex-1" />
          <ThemeSwitcher value={theme} onChange={setTheme} />
          {panel === "chat" && (
            <>
              {cid && (
                <button
                  onClick={async () => {
                    const res = await api.extractMemoriesNow(cid);
                    const saved = res.filter((r: any) => r.action === "save").length;
                    const dedup = res.filter((r: any) => r.action === "dedup").length;
                    alert(`extracted: ${saved} saved · ${dedup} duplicates skipped · ${res.length - saved - dedup} dropped`);
                  }}
                  className="px-2.5 py-1 rounded-lg text-xs border border-ink-700 hover:border-accent text-fg-mute hover:text-accent-soft transition"
                  title="ask the LLM to extract significant memories from this conversation now"
                >extract</button>
              )}
              <Toggle label="verbose" value={verbose} on={() => setVerbose((v) => !v)} />
              <Toggle label="rag" value={useRag} on={() => setUseRag((v) => !v)} />
              <Toggle label="memory" value={useMemory} on={() => setUseMemory((v) => !v)} />
              {mode === "agent" && (
                <Toggle label="auto-approve tools" value={autoApprove} on={() => setAutoApprove((v) => !v)} danger />
              )}
              <Pill>
                <select value={role} onChange={(e) => setRole(e.target.value)} className="bg-transparent outline-none">
                  <option value="auto">auto</option><option value="chat">chat</option>
                  <option value="code">code</option><option value="fast">fast</option>
                </select>
              </Pill>
              <Pill>
                <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} className="bg-transparent outline-none">
                  <option value="chat">chat</option><option value="agent">agent</option>
                </select>
              </Pill>
            </>
          )}
        </header>

        {panel === "spec" ? <SpecPanel /> : panel === "rag" ? <RagPanel /> : panel === "memory" ? <MemoryPanel /> : panel === "strategy" ? <StrategyPanel /> : panel === "settings" ? <SettingsPanel /> : (
          <>
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
              <div className="max-w-3xl mx-auto space-y-4">
                {items.length === 0 && (
                  <div className="text-center text-fg-dim mt-24">
                    <div className="text-2xl font-semibold text-fg mb-2">welcome.</div>
                    <div>ask a question, or switch to <span className="text-accent-soft">agent</span> mode.</div>
                  </div>
                )}
                {items.map((it, i) =>
                  it.kind === "user" ? <UserBubble key={i} text={it.content} /> :
                  it.kind === "assistant" ? <AssistantBubble key={i} text={it.content} meta={it.meta} /> :
                  <AgentBubble key={i} item={it} verbose={verbose} />
                )}
                {busy && <div className="text-fg-dim text-sm">▍ thinking…</div>}
              </div>
            </div>
            <Composer value={input} onChange={setInput} onSend={send} disabled={busy} mode={mode} />
          </>
        )}
      </main>
    </div>
  );
}

function Sidebar({ conversations, cid, onLoad, onNew, onDelete, panel, onPanel }: any) {
  return (
    <aside className="w-64 shrink-0 border-r border-ink-700 bg-ink-900/60 backdrop-blur p-3 flex flex-col">
      <button onClick={onNew} className="w-full mb-3 px-3 py-2 rounded-lg bg-accent hover:bg-accent-soft transition text-white font-medium shadow-glow">
        + new chat
      </button>
      <div className="text-xs uppercase tracking-wider text-fg-dim mb-1 px-1">conversations</div>
      <div className="flex-1 overflow-y-auto space-y-1">
        {conversations.map((c: any) => (
          <div key={c.id} className={`group flex items-center rounded-lg transition ${
              cid === c.id ? "bg-ink-700 text-fg ring-1 ring-accent/30" : "hover:bg-ink-800 text-fg-mute"
          }`}>
            <button onClick={() => onLoad(c.id)} className="flex-1 text-left px-3 py-2 text-sm truncate">
              {c.title || c.id.slice(0, 8)}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); if (confirm(`delete "${c.title || c.id.slice(0,8)}"?`)) onDelete(c.id); }}
              className="opacity-0 group-hover:opacity-100 px-2 text-xs text-fg-dim hover:text-red-400 transition">×</button>
          </div>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-1 text-xs">
        {(["chat", "spec", "rag", "memory", "strategy", "settings"] as Panel[]).map((p) => (
          <button key={p} onClick={() => onPanel(p)} className={`px-2 py-2 rounded-lg border transition ${
            panel === p ? "border-accent text-fg bg-ink-800 shadow-glow" : "border-ink-700 text-fg-mute hover:border-accent/40 hover:text-fg"
          }`}>{p}</button>
        ))}
      </div>
    </aside>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return <div className="bg-ink-800 border border-ink-600 rounded-lg px-2 py-1 text-sm">{children}</div>;
}

function Toggle({ label, value, on, danger }: { label: string; value: boolean; on: () => void; danger?: boolean }) {
  return (
    <button onClick={on}
      className={`px-2.5 py-1 rounded-lg text-xs border transition ${
        value
          ? danger
            ? "bg-red-500/20 border-red-500/50 text-red-200"
            : "bg-accent/20 border-accent/50 text-accent-soft"
          : "border-ink-700 text-fg-dim hover:text-fg"
      }`}>
      {value ? "● " : "○ "}{label}
    </button>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-accent text-white shadow-glow">
        <div className="prose-chat text-[0.95rem]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
      </div>
    </div>
  );
}

function AssistantBubble({ text, meta }: { text: string; meta?: api.ChatMeta }) {
  const chips: string[] = [];
  if (meta?.model) chips.push(meta.model);
  if (meta?.recalled) chips.push(`📎 ${meta.recalled} memory`);
  if (meta?.rag) chips.push(`📚 ${meta.rag} rag`);
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-ink-800/80 border border-ink-700 text-fg">
        {chips.length > 0 && (
          <div className="flex gap-2 mb-1.5 -mt-0.5 flex-wrap">
            {chips.map((c) => (
              <span key={c} className="text-[10px] uppercase tracking-wider text-fg-dim font-mono">{c}</span>
            ))}
          </div>
        )}
        <div className="prose-chat text-[0.95rem]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text || "…"}</ReactMarkdown></div>
      </div>
    </div>
  );
}

function AgentBubble({ item, verbose }: { item: Extract<ChatItem, { kind: "agent" }>; verbose: boolean }) {
  const finalAnswer = item.events.filter((e) => e.type === "token").map((e: any) => e.delta).join("");
  const reframe = item.events.find((e) => e.type === "reframe") as any;
  const clar = item.events.find((e) => e.type === "clarification_needed") as any;
  const plan = item.events.find((e) => e.type === "plan") as any;
  const planRevised = item.events.find((e) => e.type === "plan_revised") as any;
  const crit = item.events.find((e) => e.type === "critique") as any;
  const stepResults = item.events.filter((e) => e.type === "step") as any[];
  const dc = item.events.find((e) => e.type === "done_check") as any;
  const warnings = item.events.filter((e) => e.type === "warning") as any[];
  const error = item.events.find((e) => e.type === "error") as any;

  const finalPlan = planRevised || plan;

  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] w-full rounded-2xl px-4 py-3 bg-ink-800/60 border border-ink-700 space-y-3">
        <div className="text-xs text-accent-soft uppercase tracking-wider">agent run</div>
        {error && <div className="text-red-400 text-sm">⚠ {error.error}</div>}
        {warnings.map((w, i) => (
          <div key={i} className="text-xs text-amber-400">⚠ {w.stage}: {w.error}</div>
        ))}

        {reframe && verbose && <ReframeCard rf={reframe.reframe} />}
        {clar && <ClarificationCard c={clar} />}

        {finalPlan && verbose && <PlanCard plan={finalPlan.plan} revised={!!planRevised} />}
        {finalPlan && !verbose && (
          <div className="text-xs text-fg-dim">plan: {finalPlan.plan.steps.length} steps{planRevised ? " (revised)" : ""}</div>
        )}

        {crit && verbose && <CritiqueCard crit={crit.critique} />}
        {crit && !verbose && crit.critique.issues.length > 0 && (
          <div className="text-xs text-fg-dim">critique: {crit.critique.issues.length} issue(s) · {crit.critique.verdict}</div>
        )}

        {verbose && stepResults.map((s, i) => <StepCard key={i} result={s.result} />)}
        {!verbose && stepResults.length > 0 && (
          <div className="text-xs text-fg-dim">{stepResults.length} step(s) completed</div>
        )}

        {dc && <DoneCheckCard dc={dc.done_check} />}

        {finalAnswer && (
          <div className="prose-chat text-[0.95rem] pt-2 border-t border-ink-700">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{finalAnswer}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

function ReframeCard({ rf }: { rf: any }) {
  return (
    <div className="rounded-lg bg-ink-900/70 border border-ink-700 p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs uppercase tracking-wider text-fg-dim">reframe</span>
        <span className={`text-xs px-2 py-0.5 rounded ${rf.ambiguity >= 4 ? "bg-amber-900/40 text-amber-300" : "bg-ink-700 text-fg-mute"}`}>
          ambiguity {rf.ambiguity}/5
        </span>
      </div>
      <div className="text-sm text-fg mb-2">{rf.restated_goal}</div>
      {rf.assumptions?.length > 0 && (
        <ul className="text-xs text-fg-mute space-y-0.5 list-disc pl-5">
          {rf.assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}
        </ul>
      )}
    </div>
  );
}

function ClarificationCard({ c }: { c: any }) {
  return (
    <div className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-3">
      <div className="text-xs uppercase tracking-wider text-amber-400 mb-1">clarification needed (ambiguity {c.ambiguity}/5)</div>
      <div className="text-sm text-amber-100 font-medium">{c.question}</div>
      {c.assumptions?.length > 0 && (
        <details className="mt-2">
          <summary className="text-xs text-amber-300/70 cursor-pointer">assumptions I'd otherwise make</summary>
          <ul className="text-xs text-amber-200/80 mt-1 list-disc pl-5">
            {c.assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function CritiqueCard({ crit }: { crit: any }) {
  if (!crit.issues?.length) {
    return (
      <div className="rounded-lg bg-emerald-950/20 border border-emerald-700/40 p-2 text-xs text-emerald-300">
        ✓ critique: plan looks clean — {crit.summary || "no issues"}
      </div>
    );
  }
  return (
    <div className={`rounded-lg p-3 border ${crit.verdict === "revise" ? "border-amber-700/50 bg-amber-950/20" : "border-ink-700 bg-ink-900/40"}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs uppercase tracking-wider text-fg-mute">critique</span>
        <span className={`text-xs px-2 py-0.5 rounded ${crit.verdict === "revise" ? "bg-amber-900/40 text-amber-300" : "bg-ink-700 text-fg-mute"}`}>
          {crit.verdict}
        </span>
      </div>
      <ul className="space-y-1.5 text-sm">
        {crit.issues.map((iss: any, i: number) => (
          <li key={i} className="flex gap-2">
            <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded mt-0.5 ${
              iss.severity === "high" ? "bg-red-900/40 text-red-300" :
              iss.severity === "medium" ? "bg-amber-900/40 text-amber-300" :
              "bg-ink-700 text-fg-mute"}`}>{iss.severity}</span>
            <div className="flex-1">
              <div className="text-fg">{iss.concern}{iss.step != null && <span className="text-fg-dim text-xs"> (step {iss.step})</span>}</div>
              {iss.suggestion && <div className="text-xs text-fg-mute italic">→ {iss.suggestion}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DoneCheckCard({ dc }: { dc: any }) {
  const tone = dc.overall === "complete" ? "emerald" : dc.overall === "partial" ? "amber" : "red";
  const bg = { emerald: "bg-emerald-950/20 border-emerald-700/40", amber: "bg-amber-950/20 border-amber-700/50", red: "bg-red-950/20 border-red-700/50" }[tone];
  const fg = { emerald: "text-emerald-300", amber: "text-amber-300", red: "text-red-300" }[tone];
  return (
    <div className={`rounded-lg p-3 border ${bg}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs uppercase tracking-wider text-fg-mute">done check</span>
        <span className={`text-xs px-2 py-0.5 rounded uppercase ${fg}`}>{dc.overall}</span>
      </div>
      <ul className="space-y-1 text-sm">
        {dc.criteria?.map((c: any, i: number) => (
          <li key={i} className="flex gap-2">
            <span className={c.met ? "text-emerald-400" : "text-red-400"}>{c.met ? "✓" : "✗"}</span>
            <div className="flex-1">
              <div className="text-fg">{c.description}</div>
              {c.evidence && <div className="text-xs text-fg-dim italic">{c.evidence}</div>}
            </div>
          </li>
        ))}
      </ul>
      {dc.gaps?.length > 0 && (
        <div className="mt-2 pt-2 border-t border-ink-700">
          <div className="text-xs text-fg-dim uppercase tracking-wider mb-1">gaps</div>
          <ul className="text-xs text-fg-mute list-disc pl-5">
            {dc.gaps.map((g: string, i: number) => <li key={i}>{g}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function PlanCard({ plan, revised }: { plan: any; revised?: boolean }) {
  return (
    <div className="rounded-lg bg-ink-900/70 border border-ink-700 p-3">
      <div className="text-xs uppercase tracking-wider text-fg-dim mb-2">
        plan{revised ? " (revised)" : ""} · {plan.goal}
      </div>
      <ol className="space-y-1 text-sm">
        {plan.steps.map((s: any) => (
          <li key={s.n} className="flex gap-2">
            <span className="text-accent-soft font-mono">{s.n}.</span>
            <div>
              <div>{s.description}</div>
              {s.tool && <div className="text-xs text-fg-dim font-mono">→ {s.tool}({JSON.stringify(s.arguments)})</div>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StepCard({ result }: { result: any }) {
  const tr = result.tool_result;
  const ok = tr?.ok;
  return (
    <div className={`rounded-lg border p-3 ${
      tr ? (ok ? "border-emerald-700/50 bg-emerald-950/20" : "border-red-700/50 bg-red-950/20") : "border-ink-700 bg-ink-900/40"
    }`}>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-fg-dim">step {result.step.n}</span>
        <span className="text-fg">{result.step.description}</span>
        {result.step.tool && <span className="text-xs font-mono text-accent-soft">· {result.step.tool}</span>}
        {tr && <span className={`text-xs ml-auto ${ok ? "text-emerald-400" : "text-red-400"}`}>{ok ? "✓" : "✗"}</span>}
      </div>
      {tr && (tr.output || tr.error) && (
        <pre className="mt-2 text-xs font-mono text-fg bg-ink-950/60 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-64">
          {tr.output || tr.error}
        </pre>
      )}
      {!tr && result.notes && <div className="mt-1 text-xs text-fg-mute italic">{result.notes}</div>}
    </div>
  );
}

function Composer({ value, onChange, onSend, disabled, mode }: any) {
  return (
    <div className="border-t border-ink-700 p-4 bg-ink-900/40 backdrop-blur">
      <div className="max-w-3xl mx-auto flex gap-2">
        <textarea value={value} onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
          rows={1} placeholder={mode === "agent" ? "describe a goal…" : "message…"}
          className="flex-1 resize-none bg-ink-800 border border-ink-600 focus:border-accent focus:outline-none rounded-xl px-4 py-3 text-[0.95rem] placeholder:text-fg-dim" />
        <button onClick={onSend} disabled={disabled || !value.trim()}
          className="px-5 rounded-xl bg-accent hover:bg-accent-soft disabled:opacity-40 disabled:cursor-not-allowed transition font-medium">
          {mode === "agent" ? "run" : "send"}
        </button>
      </div>
    </div>
  );
}

function RagPanel() {
  const [docs, setDocs] = useState<api.RagDoc[]>([]);
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() { setDocs(await api.listRagDocs()); }
  useEffect(() => { refresh(); }, []);
  async function go(fn: () => Promise<any>) {
    setBusy(true); try { await fn(); await refresh(); } finally { setBusy(false); }
  }
  return (
    <div className="flex-1 overflow-y-auto px-6 py-6">
      <div className="max-w-3xl mx-auto space-y-6">
        <h2 className="text-xl font-semibold">RAG library</h2>
        <div className="grid grid-cols-2 gap-4">
          <Card title="ingest path (file or folder)">
            <Row v={path} setV={setPath} ph="C:\path\to\docs" busy={busy} on={() => go(async () => { await api.ingest({ path }); setPath(""); })} />
          </Card>
          <Card title="ingest URL">
            <Row v={url} setV={setUrl} ph="https://…" busy={busy} on={() => go(async () => { await api.ingest({ url }); setUrl(""); })} />
          </Card>
        </div>
        <div className="bg-ink-800/60 border border-ink-700 rounded-xl divide-y divide-ink-700">
          {docs.length === 0 && <div className="p-6 text-center text-fg-dim">no documents yet.</div>}
          {docs.map((d) => (
            <div key={d.id} className="flex items-center px-4 py-3 gap-3">
              <span className="text-xs px-2 py-0.5 rounded bg-ink-700 uppercase tracking-wide">{d.kind}</span>
              <div className="flex-1 truncate">
                <div className="text-sm">{d.title || d.source}</div>
                <div className="text-xs text-fg-dim truncate">{d.source}</div>
              </div>
              <button onClick={async () => { await api.deleteDoc(d.id); refresh(); }} className="text-xs text-fg-dim hover:text-red-400">delete</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function MemoryPanel() {
  const [mems, setMems] = useState<api.Memory[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState("fact");
  const [busy, setBusy] = useState(false);
  async function refresh() { setMems(await api.listMemories()); }
  useEffect(() => { refresh(); }, []);
  return (
    <div className="flex-1 overflow-y-auto px-6 py-6">
      <div className="max-w-3xl mx-auto space-y-6">
        <h2 className="text-xl font-semibold">long-term memory</h2>
        <div className="bg-ink-800/60 border border-ink-700 rounded-xl p-4 space-y-2">
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="a durable fact about you, the project, or a preference…"
            className="w-full bg-ink-900 border border-ink-600 rounded-lg px-3 py-2 text-sm" />
          <div className="flex gap-2">
            <select value={kind} onChange={(e) => setKind(e.target.value)} className="bg-ink-900 border border-ink-600 rounded-lg px-2 py-1 text-sm">
              <option value="fact">fact</option><option value="user">user</option>
              <option value="project">project</option><option value="preference">preference</option>
            </select>
            <button disabled={busy || !text.trim()}
              onClick={async () => { setBusy(true); try { await api.addMemory(text, kind); setText(""); await refresh(); } finally { setBusy(false); } }}
              className="px-3 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 text-sm">remember</button>
          </div>
        </div>
        <div className="bg-ink-800/60 border border-ink-700 rounded-xl divide-y divide-ink-700">
          {mems.length === 0 && <div className="p-6 text-center text-fg-dim">no memories yet.</div>}
          {mems.map((m) => (
            <div key={m.id} className="flex items-start px-4 py-3 gap-3">
              <span className="text-xs px-2 py-0.5 rounded bg-ink-700 uppercase tracking-wide mt-0.5">{m.kind}</span>
              <div className="flex-1 text-sm text-fg">{m.text}</div>
              <button onClick={async () => { await api.deleteMemory(m.id); refresh(); }} className="text-xs text-fg-dim hover:text-red-400">forget</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StrategyPanel() {
  const [items, setItems] = useState<api.Strategy[]>([]);
  const [editing, setEditing] = useState<api.Strategy | null>(null);
  const scopes: api.Scope[] = ["chat", "planner", "executor", "synthesizer", "all"];

  async function refresh() { setItems(await api.listStrategies()); }
  useEffect(() => { refresh(); }, []);

  function blank(): api.Strategy {
    return { id: "", name: "", description: "", scopes: ["all"], active: true, body: "" };
  }

  async function save() {
    if (!editing) return;
    await api.upsertStrategy(editing);
    setEditing(null);
    refresh();
  }

  return (
    <div className="flex-1 overflow-y-auto px-6 py-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold">strategies</h2>
          <span className="text-sm text-fg-dim">master-context blocks injected into agent system prompts</span>
          <div className="flex-1" />
          <button onClick={() => setEditing(blank())}
            className="px-3 py-1.5 rounded-lg bg-accent hover:bg-accent-soft text-sm">+ new</button>
        </div>

        <div className="grid grid-cols-1 gap-3">
          {items.length === 0 && <div className="p-8 text-center text-fg-dim border border-ink-700 rounded-xl">no strategies.</div>}
          {items.map((s) => (
            <div key={s.id} className={`rounded-xl border p-4 ${s.active ? "border-accent/40 bg-accent/5" : "border-ink-700 bg-ink-800/40"}`}>
              <div className="flex items-start gap-3">
                <button onClick={async () => { await api.setStrategyActive(s.id, !s.active); refresh(); }}
                  className={`mt-1 w-10 h-5 rounded-full transition ${s.active ? "bg-accent" : "bg-ink-600"}`}>
                  <div className={`w-4 h-4 rounded-full bg-white mt-0.5 transition ${s.active ? "ml-5" : "ml-0.5"}`} />
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="font-semibold">{s.name}</div>
                    <code className="text-xs text-fg-dim">{s.id}</code>
                    {s.scopes.map((sc) => (
                      <span key={sc} className="text-[10px] px-1.5 py-0.5 rounded bg-ink-700 uppercase tracking-wide">{sc}</span>
                    ))}
                  </div>
                  {s.description && <div className="text-sm text-fg-mute mt-1">{s.description}</div>}
                  <pre className="mt-2 text-xs text-fg whitespace-pre-wrap font-mono bg-ink-950/40 rounded p-2 max-h-32 overflow-y-auto">{s.body}</pre>
                </div>
                <div className="flex flex-col gap-1">
                  <button onClick={() => setEditing(s)} className="text-xs text-accent-soft hover:text-fg">edit</button>
                  <button onClick={async () => { if (confirm(`delete ${s.id}?`)) { await api.deleteStrategy(s.id); refresh(); } }}
                    className="text-xs text-fg-dim hover:text-red-400">delete</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {editing && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => setEditing(null)}>
          <div className="bg-ink-900 border border-ink-700 rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="p-5 space-y-3">
              <div className="text-lg font-semibold">{editing.id ? `edit · ${editing.id}` : "new strategy"}</div>
              <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="name" className="w-full bg-ink-800 border border-ink-600 rounded-lg px-3 py-2" />
              <input value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                placeholder="description" className="w-full bg-ink-800 border border-ink-600 rounded-lg px-3 py-2 text-sm" />
              <div className="flex gap-2 flex-wrap">
                <span className="text-sm text-fg-mute self-center">scopes:</span>
                {scopes.map((sc) => {
                  const on = editing.scopes.includes(sc);
                  return (
                    <button key={sc} onClick={() => {
                      const next = on ? editing.scopes.filter((x) => x !== sc) : [...editing.scopes, sc];
                      setEditing({ ...editing, scopes: next.length ? next : ["all"] });
                    }} className={`px-2 py-1 rounded text-xs border ${on ? "bg-accent/20 border-accent text-accent-soft" : "border-ink-700 text-fg-mute"}`}>
                      {sc}
                    </button>
                  );
                })}
              </div>
              <textarea value={editing.body} onChange={(e) => setEditing({ ...editing, body: e.target.value })}
                placeholder="body — the actual master context injected into the system prompt"
                rows={14} className="w-full bg-ink-800 border border-ink-600 rounded-lg px-3 py-2 font-mono text-sm" />
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} />
                active
              </label>
              <div className="flex gap-2 pt-2">
                <div className="flex-1" />
                <button onClick={() => setEditing(null)} className="px-4 py-2 rounded-lg border border-ink-700 hover:bg-ink-800 text-sm">cancel</button>
                <button onClick={save} disabled={!editing.name.trim()} className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 text-sm">save</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsPanel() {
  const [settings, setSettings] = useState<any>(null);
  const [draft, setDraft] = useState<any>(null);
  const [models, setModels] = useState<string[]>([]);
  const [path, setPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  async function load() {
    setErr(null);
    const [{ settings: s, overrides_path }, ms] = await Promise.all([api.getSettings(), api.listModels()]);
    setSettings(s);
    setDraft(JSON.parse(JSON.stringify(s)));
    setPath(overrides_path);
    setModels(ms);
  }
  useEffect(() => { load().catch((e) => setErr(String(e.message || e))); }, []);

  // While loading: show a real loading state. On error: surface what went
  // wrong instead of hanging on "loading…" forever (the most common cause is
  // a backend that isn't running yet).
  if (!draft) {
    if (err) {
      return (
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="max-w-md text-center space-y-3">
            <div className="text-3xl text-danger" aria-hidden>⊘</div>
            <div className="text-fg font-semibold">Couldn't load settings</div>
            <div className="text-sm text-fg-mute">{err}</div>
            <div className="text-xs text-fg-dim">
              Is the LocalAgent server running? Try <code className="font-mono text-fg-mute">localagent serve</code>.
            </div>
            <button onClick={() => { setErr(null); load().catch((e) => setErr(String(e.message || e))); }}
              className="mt-2 px-3 py-1.5 rounded-lg text-sm border border-ink-700 hover:border-accent text-fg-mute hover:text-fg transition">
              ↻ retry
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-fg-dim text-sm flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-accent animate-pulse-ring" />
          loading settings…
        </div>
      </div>
    );
  }

  function patch(path: string[], v: any) {
    setDraft((d: any) => {
      const copy = JSON.parse(JSON.stringify(d));
      let cur = copy;
      for (let i = 0; i < path.length - 1; i++) cur = cur[path[i]];
      cur[path[path.length - 1]] = v;
      return copy;
    });
  }

  function diff(): any {
    // compute a deep partial of changed fields between settings and draft
    function walk(a: any, b: any): any {
      if (a === b) return undefined;
      if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) return b;
      if (Array.isArray(a) || Array.isArray(b)) {
        return JSON.stringify(a) === JSON.stringify(b) ? undefined : b;
      }
      const out: any = {};
      const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
      for (const k of keys) {
        const sub = walk(a[k], b[k]);
        if (sub !== undefined) out[k] = sub;
      }
      return Object.keys(out).length ? out : undefined;
    }
    return walk(settings, draft) ?? {};
  }

  async function save() {
    const p = diff();
    if (!Object.keys(p).length) return;
    setSaving(true); setErr(null);
    try {
      const newSettings = await api.patchSettings(p);
      setSettings(newSettings);
      setDraft(JSON.parse(JSON.stringify(newSettings)));
      setSavedAt(Date.now());
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally { setSaving(false); }
  }

  function reset() { setDraft(JSON.parse(JSON.stringify(settings))); setErr(null); }

  const dirty = JSON.stringify(diff()) !== "{}";

  return (
    <div className="flex-1 overflow-y-auto px-6 py-6">
      <div className="max-w-3xl mx-auto space-y-5">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold">settings</h2>
          <span className="text-xs text-fg-dim font-mono truncate" title={path}>{path}</span>
          <div className="flex-1" />
          {savedAt && !dirty && <span className="text-xs text-emerald-400">saved</span>}
          <button onClick={reset} disabled={!dirty || saving}
            className="px-3 py-1.5 rounded-lg text-sm border border-ink-700 hover:bg-ink-800 disabled:opacity-30">reset</button>
          <button onClick={save} disabled={!dirty || saving}
            className="px-3 py-1.5 rounded-lg text-sm bg-accent hover:bg-accent-soft disabled:opacity-40 text-white">{saving ? "saving…" : "save"}</button>
        </div>
        {err && <div className="rounded-lg border border-red-700/50 bg-red-950/30 text-red-300 text-sm px-3 py-2">{err}</div>}

        <Section title="agent · meta-cognition">
          <Bool path={["agent", "use_reframe"]} draft={draft} set={patch} label="use reframe" hint="restate goal + assumptions before planning" />
          <Bool path={["agent", "use_critique"]} draft={draft} set={patch} label="use critique" hint="independent reviewer flags issues; one revision budget" />
          <Bool path={["agent", "use_done_check"]} draft={draft} set={patch} label="use done check" hint="honest verification at synthesis" />
          <Num path={["agent", "max_steps"]} draft={draft} set={patch} label="max steps" min={1} max={20} />
          <Num path={["agent", "json_retries"]} draft={draft} set={patch} label="json retries" min={0} max={5} />
          <Num path={["agent", "ambiguity_threshold"]} draft={draft} set={patch} label="ambiguity threshold (1-5)" min={1} max={5}
            hint="if reframe scores at-or-above this, agent asks instead of guessing" />
        </Section>

        <Section title="memory">
          <Bool path={["memory", "auto_recall"]} draft={draft} set={patch} label="auto recall" hint="inject top-k memories per turn" />
          <Bool path={["memory", "auto_save"]} draft={draft} set={patch} label="auto save" hint="LLM-extracted memories every N turns (background)" />
          <Num path={["memory", "auto_save_every_turns"]} draft={draft} set={patch} label="auto save cadence (user turns)" min={1} max={50} />
          <Num path={["memory", "auto_save_min_importance"]} draft={draft} set={patch} label="min importance to save (1-5)" min={1} max={5} />
          <Num path={["memory", "auto_save_dedup_threshold"]} draft={draft} set={patch} label="dedup similarity threshold" min={0} max={1} step={0.01}
            hint="cosine similarity above this counts as duplicate" />
          <Num path={["memory", "auto_save_window"]} draft={draft} set={patch} label="extraction window (messages)" min={2} max={50} />
          <Num path={["memory", "recall_k"]} draft={draft} set={patch} label="recall k" min={0} max={20} />
        </Section>

        <Section title="tools · safety">
          <Bool path={["tools", "allow_shell"]} draft={draft} set={patch} label="allow shell exec" danger />
          <Bool path={["tools", "allow_python_exec"]} draft={draft} set={patch} label="allow python exec" danger />
          <Bool path={["tools", "allow_file_write"]} draft={draft} set={patch} label="allow file write" />
          <Bool path={["tools", "allow_web_fetch"]} draft={draft} set={patch} label="allow web fetch" />
          <Num path={["tools", "shell_timeout_s"]} draft={draft} set={patch} label="shell timeout (s)" min={1} max={600} />
          <Num path={["tools", "python_timeout_s"]} draft={draft} set={patch} label="python timeout (s)" min={1} max={600} />
          <Tags path={["tools", "require_confirmation"]} draft={draft} set={patch} label="require confirmation" hint="tool names that need approval" />
        </Section>

        <Section title="models">
          {(["chat", "code", "fast", "router", "planner", "executor", "memory_extractor", "embed"] as const).map((role) => (
            <Select key={role} path={["models", role]} draft={draft} set={patch} label={role} options={models} />
          ))}
        </Section>

        <Section title="rag">
          <Num path={["rag", "chunk_size"]} draft={draft} set={patch} label="chunk size" min={50} max={4000} />
          <Num path={["rag", "chunk_overlap"]} draft={draft} set={patch} label="chunk overlap" min={0} max={1000} />
          <Num path={["rag", "top_k"]} draft={draft} set={patch} label="top k" min={1} max={50} />
          <Num path={["rag", "embed_dim"]} draft={draft} set={patch} label="embed dim" min={64} max={4096} hint="must match the embed model; restart usually needed" />
        </Section>

        <Section title="general">
          <Select path={["default_role"]} draft={draft} set={patch} label="default role" options={["auto", "chat", "code", "fast"]} />
          <Text path={["system_prompt"]} draft={draft} set={patch} label="system prompt" textarea rows={3} />
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-ink-800/40 border border-ink-700 rounded-xl p-4">
      <div className="text-xs uppercase tracking-wider text-fg-dim mb-3">{title}</div>
      <div className="grid gap-3">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="grid grid-cols-[180px_1fr] items-center gap-3">
      <div>
        <div className="text-sm text-fg">{label}</div>
        {hint && <div className="text-xs text-fg-dim">{hint}</div>}
      </div>
      <div>{children}</div>
    </label>
  );
}

function getPath(o: any, p: string[]) { return p.reduce((a, k) => (a == null ? a : a[k]), o); }

function Bool({ path, draft, set, label, hint, danger }: any) {
  const v = !!getPath(draft, path);
  return (
    <Field label={label} hint={hint}>
      <button onClick={() => set(path, !v)}
        className={`w-12 h-6 rounded-full transition relative ${v ? (danger ? "bg-red-500" : "bg-accent") : "bg-ink-600"}`}>
        <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all ${v ? "left-6" : "left-0.5"}`} />
      </button>
    </Field>
  );
}

function Num({ path, draft, set, label, hint, min, max, step }: any) {
  const v = getPath(draft, path);
  return (
    <Field label={label} hint={hint}>
      <input type="number" value={v ?? ""} min={min} max={max} step={step ?? 1}
        onChange={(e) => set(path, step && step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10))}
        className="w-32 bg-ink-900 border border-ink-600 rounded-lg px-3 py-1.5 text-sm font-mono focus:border-accent focus:outline-none" />
    </Field>
  );
}

function Text({ path, draft, set, label, hint, textarea, rows }: any) {
  const v = getPath(draft, path) ?? "";
  if (textarea) return (
    <Field label={label} hint={hint}>
      <textarea rows={rows ?? 2} value={v} onChange={(e) => set(path, e.target.value)}
        className="w-full bg-ink-900 border border-ink-600 rounded-lg px-3 py-1.5 text-sm focus:border-accent focus:outline-none" />
    </Field>
  );
  return (
    <Field label={label} hint={hint}>
      <input type="text" value={v} onChange={(e) => set(path, e.target.value)}
        className="w-full bg-ink-900 border border-ink-600 rounded-lg px-3 py-1.5 text-sm focus:border-accent focus:outline-none" />
    </Field>
  );
}

function Select({ path, draft, set, label, options, hint }: any) {
  const v = getPath(draft, path) ?? "";
  return (
    <Field label={label} hint={hint}>
      <div className="flex gap-2 items-center">
        <select value={v} onChange={(e) => set(path, e.target.value)}
          className="bg-ink-900 border border-ink-600 rounded-lg px-3 py-1.5 text-sm focus:border-accent focus:outline-none">
          {!options.includes(v) && <option value={v}>{v} (custom)</option>}
          {options.map((o: string) => <option key={o} value={o}>{o}</option>)}
        </select>
        <input type="text" value={v} onChange={(e) => set(path, e.target.value)} placeholder="or type"
          className="flex-1 bg-ink-900 border border-ink-600 rounded-lg px-3 py-1.5 text-sm font-mono focus:border-accent focus:outline-none" />
      </div>
    </Field>
  );
}

function Tags({ path, draft, set, label, hint }: any) {
  const v: string[] = getPath(draft, path) ?? [];
  const [input, setInput] = useState("");
  return (
    <Field label={label} hint={hint}>
      <div className="flex gap-1.5 flex-wrap">
        {v.map((t, i) => (
          <span key={i} className="text-xs px-2 py-1 rounded bg-ink-700 text-fg flex items-center gap-1">
            {t}
            <button onClick={() => set(path, v.filter((_, j) => j !== i))} className="text-fg-dim hover:text-red-400">×</button>
          </span>
        ))}
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              e.preventDefault();
              set(path, [...v, input.trim()]); setInput("");
            }
          }}
          placeholder="add… (enter)"
          className="bg-ink-900 border border-ink-600 rounded px-2 py-1 text-xs focus:border-accent focus:outline-none w-32" />
      </div>
    </Field>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-ink-800/60 border border-ink-700 rounded-xl p-4">
      <div className="text-sm text-fg-mute mb-2">{title}</div>
      {children}
    </div>
  );
}
function Row({ v, setV, ph, busy, on }: any) {
  return (
    <div className="flex gap-2">
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder={ph}
        className="flex-1 bg-ink-900 border border-ink-600 rounded-lg px-3 py-2 text-sm" />
      <button onClick={on} disabled={busy} className="px-3 rounded-lg bg-accent hover:bg-accent-soft disabled:opacity-40 text-sm">add</button>
    </div>
  );
}
