export type Conversation = { id: string; title: string | null; created_at: number; updated_at: number };
export type ChatMessage = { role: "system" | "user" | "assistant" | "tool"; content: string };
export type RagDoc = { id: string; source: string; title: string | null; kind: string; created_at: number };
export type Memory = { id: string; text: string; kind: string; created_at: number; metadata?: any };

const base = "/api";

export async function listModels(): Promise<string[]> {
  const r = await fetch(`${base}/models`);
  return (await r.json()).models;
}
export async function listConversations(): Promise<Conversation[]> {
  const r = await fetch(`${base}/conversations`);
  return (await r.json()).conversations;
}
export async function newConversation(): Promise<string> {
  const r = await fetch(`${base}/conversations`, { method: "POST" });
  return (await r.json()).id;
}
export async function loadMessages(cid: string): Promise<ChatMessage[]> {
  const r = await fetch(`${base}/conversations/${cid}/messages`);
  return (await r.json()).messages;
}
export type ChatMeta = { cid?: string; model?: string; recalled?: number; rag?: number };

export async function* streamChat(opts: {
  conversation_id: string | null; message: string; role: string; use_rag: boolean; use_memory?: boolean;
}): AsyncGenerator<{ delta?: string; meta?: ChatMeta }> {
  const r = await fetch(`${base}/chat/stream`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(opts),
  });
  const meta: ChatMeta = {
    cid: r.headers.get("x-conversation-id") ?? undefined,
    model: r.headers.get("x-model") ?? undefined,
    recalled: Number(r.headers.get("x-memory-recalled") ?? 0),
    rag: Number(r.headers.get("x-rag-recalled") ?? 0),
  };
  yield { meta };
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    yield { delta: dec.decode(value) };
  }
}

export async function deleteConversation(cid: string) {
  await fetch(`${base}/conversations/${cid}`, { method: "DELETE" });
}

export async function extractMemoriesNow(cid: string): Promise<any[]> {
  const r = await fetch(`${base}/conversations/${cid}/extract-memories`, { method: "POST" });
  return (await r.json()).results;
}
export async function* runAgent(goal: string, autoApprove: boolean): AsyncGenerator<any> {
  const r = await fetch(`${base}/agent/run`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ goal, auto_approve: autoApprove }),
  });
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const ln of lines) if (ln.trim()) yield JSON.parse(ln);
  }
}
export async function listRagDocs(): Promise<RagDoc[]> {
  const r = await fetch(`${base}/rag/documents`);
  return (await r.json()).documents;
}
export async function ingest(input: { path?: string; url?: string }) {
  const r = await fetch(`${base}/rag/ingest`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input),
  });
  return r.json();
}
export async function deleteDoc(id: string) {
  await fetch(`${base}/rag/documents/${id}`, { method: "DELETE" });
}
export async function listMemories(): Promise<Memory[]> {
  const r = await fetch(`${base}/memory`);
  return (await r.json()).memories;
}
export async function addMemory(text: string, kind = "fact"): Promise<string> {
  const r = await fetch(`${base}/memory`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text, kind }),
  });
  return (await r.json()).id;
}
export async function deleteMemory(id: string) {
  await fetch(`${base}/memory/${id}`, { method: "DELETE" });
}

export type Scope = "chat" | "planner" | "executor" | "synthesizer" | "all";
export type Strategy = {
  id: string; name: string; description: string;
  scopes: Scope[]; active: boolean; body: string;
};

export async function listStrategies(): Promise<Strategy[]> {
  const r = await fetch(`${base}/strategies`);
  return (await r.json()).strategies;
}
export async function upsertStrategy(s: Partial<Strategy>): Promise<Strategy> {
  const r = await fetch(`${base}/strategies`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(s),
  });
  return r.json();
}
export async function setStrategyActive(id: string, active: boolean) {
  await fetch(`${base}/strategies/${id}/active?active=${active}`, { method: "POST" });
}
export async function deleteStrategy(id: string) {
  await fetch(`${base}/strategies/${id}`, { method: "DELETE" });
}
export async function previewStrategy(scope: Scope): Promise<string> {
  const r = await fetch(`${base}/strategies/preview/${scope}`);
  return (await r.json()).text;
}

export type Settings = any;  // stays loose; backend is the schema authority
export async function getSettings(): Promise<{ settings: Settings; overrides_path: string }> {
  const r = await fetch(`${base}/settings`);
  return r.json();
}
export async function patchSettings(patch: any): Promise<Settings> {
  const r = await fetch(`${base}/settings`, {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const msg = await r.text();
    throw new Error(`settings update failed (${r.status}): ${msg}`);
  }
  return (await r.json()).settings;
}

// ── spec-driven development ─────────────────────────────────

export type ChunkStatus = "pending" | "in_progress" | "completed" | "blocked" | "skipped";
export type SpecStatus = "draft" | "ready" | "executing" | "verified" | "partial" | "failed";

export type AcceptanceCriterion = {
  id: string;
  text: string;
  verification: string;
  met: boolean | null;
  evidence: string;
};

export type ClarifyingQuestion = {
  n: number;
  text: string;
  why: string;
  importance: number;
  kind: "binary" | "choice" | "value";
  choices: string[];
  answer: string | null;
};

export type WorkChunk = {
  n: number;
  title: string;
  description: string;
  file_hints: string[];
  acceptance: AcceptanceCriterion[];
  status: ChunkStatus;
  notes: string;
  attempts: number;
  last_error: string;
};

export type SpecReadiness = {
  score: number; ready: boolean; blockers: string[]; summary: string;
};

export type SpecVerification = {
  overall: "verified" | "partial" | "failed";
  chunks_completed: number; chunks_total: number;
  criteria_met: number; criteria_total: number;
  gaps: string[];
};

export type Spec = {
  id: string;
  title: string;
  goal: string;
  summary: string;
  requirements: string[];
  constraints: string[];
  out_of_scope: string[];
  open_questions: ClarifyingQuestion[];
  work_chunks: WorkChunk[];
  global_acceptance: AcceptanceCriterion[];
  history: string[];
  readiness: SpecReadiness | null;
  verification: SpecVerification | null;
  status: SpecStatus;
  created_at: number;
  updated_at: number;
  rounds: number;
};

export type SpecRow = {
  id: string; title: string; status: SpecStatus;
  updated_at: number; chunks: number; rounds: number;
};

export async function listSpecs(): Promise<SpecRow[]> {
  const r = await fetch(`${base}/specs`);
  return (await r.json()).specs;
}
export async function getSpec(sid: string): Promise<Spec> {
  const r = await fetch(`${base}/specs/${sid}`);
  if (!r.ok) throw new Error(`spec ${sid} not found`);
  return r.json();
}
export async function deleteSpec(sid: string) {
  await fetch(`${base}/specs/${sid}`, { method: "DELETE" });
}
export async function startSpec(goal: string, opts?: { title_hint?: string; max_rounds?: number }): Promise<Spec> {
  const r = await fetch(`${base}/specs`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ goal, ...opts }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function specQuestions(sid: string): Promise<{ spec: Spec; questions: ClarifyingQuestion[] }> {
  const r = await fetch(`${base}/specs/${sid}/questions`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function specAnswer(sid: string, answers: { n: number; answer: string }[]): Promise<Spec> {
  const r = await fetch(`${base}/specs/${sid}/answer`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ answers }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function specReadiness(sid: string): Promise<{ spec: Spec; readiness: SpecReadiness }> {
  const r = await fetch(`${base}/specs/${sid}/readiness`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function specForceReady(sid: string, reason = "user override"): Promise<Spec> {
  const r = await fetch(`${base}/specs/${sid}/ready`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function specDecompose(sid: string): Promise<Spec> {
  const r = await fetch(`${base}/specs/${sid}/decompose`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function* specExecute(sid: string, autoApprove = true): AsyncGenerator<any> {
  const r = await fetch(`${base}/specs/${sid}/execute?auto_approve=${autoApprove}`, { method: "POST" });
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const ln of lines) if (ln.trim()) yield JSON.parse(ln);
  }
}
