"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiRequest } from "@/lib/api";

type HistoryItem = { id: string; recruiter_name: string | null; recruiter_email: string; company: string | null; subject: string; timestamp: string; delivery_state: "sent" | "uncertain"; follow_up_count: number; uncertain_follow_up_count: number; can_follow_up: boolean };
type Message = { id: string; kind: "original" | "follow_up"; sender_email: string; subject: string; body: string; timestamp: string; delivery_state: "sent" | "uncertain"; attachment_filename: string | null; attachment_size_bytes: number | null };
type Conversation = { id: string; recruiter_name: string | null; recruiter_email: string; company: string | null; subject: string; can_follow_up: boolean; messages: Message[] };

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) return "The free AI model is busy. Wait a moment, then try again.";
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}
function formatBytes(bytes: number): string { return bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`; }

export function HistoryDashboard() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [guidance, setGuidance] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (term = "") => {
    setLoading(true); setError(null);
    try { setItems(await apiRequest<HistoryItem[]>(`/api/history${term.trim() ? `?search=${encodeURIComponent(term.trim())}` : ""}`)); }
    catch (loadError) { setError(errorMessage(loadError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function openConversation(id: string) {
    setBusy(`open-${id}`); setError(null); setGuidance("");
    try { setConversation(await apiRequest<Conversation>(`/api/history/${id}`)); }
    catch (openError) { setError(errorMessage(openError)); }
    finally { setBusy(null); }
  }
  async function generateFollowup() {
    if (!conversation?.can_follow_up || busy) return;
    setBusy("generate"); setError(null);
    try {
      const draft = await apiRequest<{ id: string }>("/api/followups/generate", { method: "POST", body: JSON.stringify({ conversation_id: conversation.id, guidance: guidance.trim() || null }) });
      window.location.assign(`/?draft=${encodeURIComponent(draft.id)}`);
    } catch (generationError) { setError(errorMessage(generationError)); }
    finally { setBusy(null); }
  }

  return <div className="space-y-5">
    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
    <form className="card flex gap-3 p-4" onSubmit={(event) => { event.preventDefault(); void load(search); }}><label htmlFor="history-search" className="sr-only">Search sent email history</label><input id="history-search" className="field" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search recipient, company, or subject" maxLength={200} /><button className="button-secondary" disabled={loading}>Search</button></form>
    {loading ? <div className="card p-8 text-slate-600" role="status">Loading email history…</div> : items.length === 0 ? <div className="empty-panel"><h2 className="text-xl font-semibold">No RecruitFlow email history yet</h2><p className="mt-2 text-slate-600">Only messages dispatched through RecruitFlow appear here.</p></div> : <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
      <section className="card h-fit p-4" aria-label="Email conversations"><div className="space-y-2">{items.map((item) => <button key={item.id} type="button" onClick={() => void openConversation(item.id)} className={`w-full rounded-xl border p-4 text-left ${conversation?.id === item.id ? "border-blue-300 bg-blue-50" : "border-slate-200 hover:bg-slate-50"}`}><span className="flex items-center justify-between gap-2"><span className="truncate font-semibold text-slate-900">{item.recruiter_name || item.recruiter_email}</span><Status state={item.delivery_state} /></span><span className="mt-1 block truncate text-sm text-slate-700">{item.subject}</span><span className="mt-2 block text-xs text-slate-500">{item.company || "No company"} · {new Date(item.timestamp).toLocaleString()} · {item.follow_up_count} follow-up{item.follow_up_count === 1 ? "" : "s"}</span>{item.uncertain_follow_up_count > 0 && <span className="mt-1 block text-xs font-semibold text-amber-700">{item.uncertain_follow_up_count} uncertain follow-up</span>}</button>)}</div></section>
      {!conversation ? <section className="card p-8 text-center text-slate-600">Select a conversation to view its RecruitFlow messages.</section> : <section className="space-y-5"><div className="card p-5 sm:p-7"><h2 className="text-lg font-bold text-slate-950">{conversation.subject}</h2><p className="mt-1 text-sm text-slate-600">To {conversation.recruiter_name || conversation.recruiter_email}{conversation.company ? ` at ${conversation.company}` : ""}</p></div>{conversation.messages.map((message) => <article key={message.id} className="card p-5 sm:p-7"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-bold">{message.kind === "original" ? "Original email" : "Follow-up"}</h3><Status state={message.delivery_state} /></div><p className="mt-1 text-xs text-slate-500">{new Date(message.timestamp).toLocaleString()} · From {message.sender_email}</p><p className="mt-4 font-semibold text-slate-900">{message.subject}</p><div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-700">{message.body}</div><p className="mt-4 text-xs text-slate-500">Attachment: {message.attachment_filename ? `${message.attachment_filename} (${formatBytes(message.attachment_size_bytes || 0)})` : "None"}</p></article>)}<div className="card p-5 sm:p-7"><h3 className="font-bold">Generate follow-up</h3>{conversation.can_follow_up ? <><label htmlFor="followup-guidance" className="mt-4 block text-sm font-semibold text-slate-700">Optional guidance</label><textarea id="followup-guidance" className="field mt-2 min-h-24 resize-y" value={guidance} onChange={(event) => setGuidance(event.target.value)} maxLength={2000} placeholder="e.g. Keep it brief and reiterate my Java experience." /><p className="mt-2 text-xs text-slate-500">RecruitFlow has not read any replies. The AI sees only messages RecruitFlow sent in this thread.</p><button type="button" className="button-primary mt-4" onClick={() => void generateFollowup()} disabled={busy !== null}>{busy === "generate" ? "Generating…" : "Generate Follow-Up"}</button></> : <p className="mt-2 text-sm text-amber-700">Follow-up is unavailable for uncertain delivery, a disconnected/different Gmail account, or older messages without stored threading headers.</p>}</div></section>}
    </div>}
  </div>;
}

function Status({ state }: { state: "sent" | "uncertain" }) { return <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${state === "sent" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>{state === "sent" ? "Sent" : "Uncertain"}</span>; }
