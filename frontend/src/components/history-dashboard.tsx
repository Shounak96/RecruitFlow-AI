"use client";

import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

type HistoryItem = { id: string; recruiter_name: string | null; recruiter_email: string; company: string | null; subject: string; timestamp: string; delivery_state: "sent" | "uncertain"; follow_up_count: number; uncertain_follow_up_count: number; can_follow_up: boolean; unread_reply_count: number };
type Message = { id: string; kind: "original" | "follow_up" | "received"; direction: "outgoing" | "incoming"; sender_email: string; subject: string; body: string; full_body: string | null; timestamp: string; delivery_state: "sent" | "uncertain" | null; attachment_filename: string | null; attachment_size_bytes: number | null };
type ReplyEligibility = "eligible" | "no_received_message" | "draft_exists" | "already_replied" | "active_send" | "gmail_unavailable" | "unsupported_inbound";
type Conversation = { id: string; recruiter_name: string | null; recruiter_email: string; company: string | null; subject: string; can_follow_up: boolean; unread_reply_count: number; has_received_messages: boolean; can_reply: boolean; reply_eligibility: ReplyEligibility; reply_draft_id: string | null; messages: Message[] };
type GmailStatus = { connected: boolean; email: string | null; read_authorized: boolean; reauthorization_required: boolean };
type SyncResult = { threads_checked: number; threads_failed: number; new_messages: number; partial_failure: boolean };
type GmailAuthorization = { authorization_url: string };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}
function formatBytes(bytes: number): string { return bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`; }

export function HistoryDashboard() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [gmail, setGmail] = useState<GmailStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [guidance, setGuidance] = useState("");
  const [replyInstructions, setReplyInstructions] = useState("");
  const [expandedMessages, setExpandedMessages] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(async (term = "") => {
    setLoading(true); setError(null);
    try {
      const [history, status] = await Promise.all([
        apiRequest<HistoryItem[]>(`/api/history${term.trim() ? `?search=${encodeURIComponent(term.trim())}` : ""}`),
        apiRequest<GmailStatus>("/api/gmail/status"),
      ]);
      setItems(history); setGmail(status);
    } catch (loadError) { setError(errorMessage(loadError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function openConversation(id: string) {
    setBusy(`open-${id}`); setError(null); setSuccess(null); setGuidance(""); setReplyInstructions(""); setExpandedMessages([]);
    try {
      const detail = await apiRequest<Conversation>(`/api/history/${id}`);
      setConversation(detail);
      if (detail.unread_reply_count > 0) {
        await apiRequest(`/api/history/${id}/received/read`, { method: "POST" });
        setConversation({ ...detail, unread_reply_count: 0 });
        setItems((previous) => previous.map((item) => item.id === id ? { ...item, unread_reply_count: 0 } : item));
      }
    } catch (openError) { setError(errorMessage(openError)); }
    finally { setBusy(null); }
  }
  async function connectGmail() {
    setBusy("connect"); setError(null);
    try {
      const result = await apiRequest<GmailAuthorization>("/api/gmail/oauth/authorize");
      window.location.assign(result.authorization_url);
    } catch (connectError) { setError(errorMessage(connectError)); setBusy(null); }
  }
  async function syncReplies() {
    if (!gmail?.read_authorized || busy) return;
    setBusy("sync"); setError(null); setSuccess(null);
    try {
      const result = await apiRequest<SyncResult>("/api/gmail/replies/sync", { method: "POST" });
      await load(search);
      if (conversation) setConversation(await apiRequest<Conversation>(`/api/history/${conversation.id}`));
      const base = result.new_messages === 1 ? "1 new reply synced." : `${result.new_messages} new replies synced.`;
      setSuccess(result.partial_failure ? `${base} ${result.threads_failed} of ${result.threads_checked} threads could not be checked; try again safely.` : base);
    } catch (syncError) { setError(errorMessage(syncError)); }
    finally { setBusy(null); }
  }
  async function deleteConversation(id: string) {
    if (busy || !window.confirm("Remove this conversation from RecruitFlow history? The original, replies, and follow-ups will be permanently hidden in RecruitFlow, but no Gmail messages will be deleted or changed.")) return;
    setBusy(`delete-${id}`); setError(null); setSuccess(null);
    try {
      await apiRequest(`/api/history/${id}`, { method: "DELETE" });
      setItems((previous) => previous.filter((item) => item.id !== id));
      if (conversation?.id === id) setConversation(null);
      setSuccess("Conversation removed from RecruitFlow history. Gmail was not changed.");
    } catch (deleteError) { setError(errorMessage(deleteError)); }
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
  async function generateReply() {
    if (!conversation?.can_reply || busy) return;
    setBusy("reply"); setError(null); setSuccess(null);
    try {
      const draft = await apiRequest<{ id: string }>("/api/replies/generate", { method: "POST", body: JSON.stringify({ conversation_id: conversation.id, instructions: replyInstructions.trim() || null }) });
      window.location.assign(`/?draft=${encodeURIComponent(draft.id)}`);
    } catch (generationError) { setError(errorMessage(generationError)); }
    finally { setBusy(null); }
  }

  return <div className="space-y-5">
    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
    {success && <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">{success}</div>}
    <div className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <form className="flex flex-1 gap-3" onSubmit={(event) => { event.preventDefault(); void load(search); }}><label htmlFor="history-search" className="sr-only">Search sent email history</label><input id="history-search" className="field" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search recipient, company, or subject" maxLength={200} /><button className="button-secondary" disabled={loading}>Search</button></form>
      {gmail?.read_authorized ? <button type="button" className="button-secondary shrink-0" onClick={() => void syncReplies()} disabled={busy !== null}>{busy === "sync" ? "Syncing replies…" : "Sync Replies"}</button> : <button type="button" className="button-secondary shrink-0" onClick={() => void connectGmail()} disabled={busy !== null}>{gmail?.connected ? "Reconnect Gmail for replies" : "Connect Gmail"}</button>}
    </div>
    {gmail?.reauthorization_required && <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">Your existing Gmail connection can still send, but it lacks read-only permission. Reconnect Gmail to sync replies.</p>}
    {loading ? <div className="card p-8 text-slate-600" role="status">Loading email history…</div> : items.length === 0 ? <div className="empty-panel"><h2 className="text-xl font-semibold">No RecruitFlow email history yet</h2><p className="mt-2 text-slate-600">Only messages dispatched through RecruitFlow appear here.</p></div> : <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
      <section className="card h-fit p-4" aria-label="Email conversations"><div className="space-y-2">{items.map((item) => <div key={item.id} className={`rounded-xl border ${conversation?.id === item.id ? "border-blue-300 bg-blue-50" : "border-slate-200"}`}><button type="button" onClick={() => void openConversation(item.id)} disabled={busy !== null} className="w-full rounded-t-xl p-4 text-left hover:bg-slate-50 disabled:cursor-not-allowed"><span className="flex items-center justify-between gap-2"><span className="truncate font-semibold text-slate-900">{item.recruiter_name || item.recruiter_email}</span><Status state={item.delivery_state} /></span><span className="mt-1 block truncate text-sm text-slate-700">{item.subject}</span><span className="mt-2 block text-xs text-slate-500">{item.company || "No company"} · {new Date(item.timestamp).toLocaleString()} · {item.follow_up_count} follow-up{item.follow_up_count === 1 ? "" : "s"}</span>{item.unread_reply_count > 0 && <span className="mt-2 inline-flex rounded-full bg-blue-100 px-2.5 py-1 text-xs font-bold text-blue-800">{item.unread_reply_count} new repl{item.unread_reply_count === 1 ? "y" : "ies"}</span>}{item.uncertain_follow_up_count > 0 && <span className="mt-1 block text-xs font-semibold text-amber-700">{item.uncertain_follow_up_count} uncertain follow-up</span>}</button><button type="button" onClick={() => void deleteConversation(item.id)} disabled={busy !== null} className="w-full rounded-b-xl border-t border-slate-200 px-4 py-2 text-left text-xs font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60" aria-label={`Delete conversation: ${item.subject}`}>{busy === `delete-${item.id}` ? "Deleting…" : "Delete conversation"}</button></div>)}</div></section>
      {!conversation ? <section className="card p-8 text-center text-slate-600">Select a conversation to view its RecruitFlow messages and synced replies.</section> : <section className="space-y-5">
        <div className="card p-5 sm:p-7"><h2 className="text-lg font-bold text-slate-950">{conversation.subject}</h2><p className="mt-1 text-sm text-slate-600">Conversation with {conversation.recruiter_name || conversation.recruiter_email}{conversation.company ? ` at ${conversation.company}` : ""}</p></div>
        {conversation.messages.map((message) => { const showFull = Boolean(message.full_body && expandedMessages.includes(message.id)); return <article key={message.id} className={`card p-5 sm:p-7 ${message.direction === "incoming" ? "border-blue-200 bg-blue-50/40" : ""}`}><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-bold">{message.kind === "original" ? "Original email" : message.kind === "follow_up" ? "Outgoing follow-up" : "Incoming reply"}</h3>{message.delivery_state ? <Status state={message.delivery_state} /> : <span className="rounded-full bg-blue-100 px-2.5 py-1 text-xs font-bold text-blue-800">Received</span>}</div><p className="mt-1 text-xs text-slate-500">{new Date(message.timestamp).toLocaleString()} · From {message.sender_email}</p><p className="mt-4 font-semibold text-slate-900">{message.subject}</p><div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-700">{(showFull ? message.full_body : message.body) || "No readable text content."}</div>{message.full_body && <button type="button" className="mt-3 text-xs font-semibold text-blue-700 underline" aria-expanded={showFull} onClick={() => setExpandedMessages((current) => showFull ? current.filter((id) => id !== message.id) : [...current, message.id])}>{showFull ? "Show cleaned reply" : "Show full message"}</button>}{message.direction === "outgoing" && <p className="mt-4 text-xs text-slate-500">Attachment: {message.attachment_filename ? `${message.attachment_filename} (${formatBytes(message.attachment_size_bytes || 0)})` : "None"}</p>}</article>; })}
        {conversation.has_received_messages && <div className="card border-blue-200 p-5 sm:p-7"><h3 className="font-bold text-blue-950">Reply to the latest recruiter message</h3><p className="mt-2 text-sm text-slate-600">AI uses the chronological sent and received messages shown above. The proposed reply opens in Compose for editing, refinement, final review, and explicit approval.</p>{conversation.can_reply ? <><label htmlFor="reply-instructions" className="mt-4 block text-sm font-semibold text-slate-700">Instructions for my reply (optional)</label><textarea id="reply-instructions" className="field mt-2 min-h-24 resize-y" value={replyInstructions} onChange={(event) => setReplyInstructions(event.target.value)} maxLength={2000} placeholder="e.g. I'm free Wednesday after 2 PM, or ask whether the role is remote." /><button type="button" className="button-primary mt-4" onClick={() => void generateReply()} disabled={busy !== null}>{busy === "reply" ? "Generating AI reply…" : "Generate AI Reply"}</button></> : <ReplyEligibilityNotice conversation={conversation} />}</div>}
        <div className="card p-5 sm:p-7"><h3 className="font-bold">Send another follow-up</h3>{conversation.can_follow_up ? <><label htmlFor="followup-guidance" className="mt-4 block text-sm font-semibold text-slate-700">Optional follow-up guidance</label><textarea id="followup-guidance" className="field mt-2 min-h-24 resize-y" value={guidance} onChange={(event) => setGuidance(event.target.value)} maxLength={2000} placeholder="e.g. Keep it brief and reiterate my Java experience." /><p className="mt-2 text-xs text-slate-500">This action uses only RecruitFlow's outgoing messages. Use Generate AI Reply when responding to a synced incoming message.</p><button type="button" className="button-secondary mt-4" onClick={() => void generateFollowup()} disabled={busy !== null}>{busy === "generate" ? "Generating follow-up…" : "Generate Follow-Up"}</button></> : <p className="mt-2 text-sm text-amber-700">Follow-up is unavailable for uncertain delivery, a disconnected/different Gmail account, or older messages without stored threading headers.</p>}</div>
      </section>}
    </div>}
  </div>;
}

function Status({ state }: { state: "sent" | "uncertain" }) { return <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${state === "sent" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>{state === "sent" ? "Sent" : "Uncertain"}</span>; }

function ReplyEligibilityNotice({ conversation }: { conversation: Conversation }) {
  if (conversation.reply_eligibility === "already_replied") return <p className="mt-3 text-sm font-medium text-slate-600">Already replied. Waiting for a new message.</p>;
  if (conversation.reply_eligibility === "draft_exists" && conversation.reply_draft_id) return <div className="mt-3 flex flex-wrap items-center gap-3"><p className="text-sm text-slate-700">An unsent reply draft already exists for this message.</p><a className="button-secondary" href={`/?draft=${encodeURIComponent(conversation.reply_draft_id)}`}>Open draft</a></div>;
  if (conversation.reply_eligibility === "active_send") return <p className="mt-3 text-sm text-amber-800">A send is pending, in progress, or has uncertain delivery. Check its status before replying again.</p>;
  if (conversation.reply_eligibility === "gmail_unavailable") return <p className="mt-3 text-sm text-amber-800">Reconnect the original Gmail account with send permission before creating a reply.</p>;
  if (conversation.reply_eligibility === "unsupported_inbound") return <p className="mt-3 text-sm text-amber-800">The latest message sender, recipient, or thread metadata cannot be safely verified for a reply.</p>;
  return <p className="mt-3 text-sm text-slate-600">No eligible recruiter reply is available yet.</p>;
}
