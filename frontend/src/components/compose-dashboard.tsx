"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type Purpose = { id: string; title: string; description: string; is_default: boolean };
type Resume = { id: string; display_name: string; is_default: boolean };
type Draft = {
  id: string; recipient_name: string; recipient_email: string; company: string | null;
  role: string | null; job_description: string | null; purpose_id: string;
  resume_id: string | null; attach_resume: boolean; subject: string; body: string;
  created_at: string; updated_at: string; status: "not_sent" | "sent";
  refinement_allowed: boolean;
  conversation_id: string | null;
};
type GmailStatus = { connected: boolean; email: string | null };
type SendReview = {
  id: string; draft_id: string; status: "pending" | "sending" | "sent" | "uncertain" | "failed" | "stale";
  sender_email: string; recipient_email: string; subject: string; body: string;
  attachment: { filename: string; size_bytes: number } | null; created_at: string;
};
type SendResult = { status: "sent" | "uncertain" | "failed" };
type EmailRevision = { subject: string; body: string };

const emptyForm = {
  recipient_name: "", recipient_email: "", company: "", role: "", job_description: "",
  purpose_id: "", resume_id: "", attach_resume: false, subject: "", body: "",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) return "The free AI model is busy. Wait a moment, then try again.";
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ComposeDashboard() {
  const [purposes, setPurposes] = useState<Purpose[]>([]);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [currentStatus, setCurrentStatus] = useState<"not_sent" | "sent">("not_sent");
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [refinementAllowed, setRefinementAllowed] = useState(true);
  const [gmail, setGmail] = useState<GmailStatus>({ connected: false, email: null });
  const [review, setReview] = useState<SendReview | null>(null);
  const [approved, setApproved] = useState(false);
  const [refinementInstruction, setRefinementInstruction] = useState("");
  const [revision, setRevision] = useState<EmailRevision | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"generate" | "refine" | "save" | "delete" | "review" | "send" | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const saveIsValid = Boolean(
    currentId
      && form.recipient_name.trim()
      && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.recipient_email.trim())
      && form.purpose_id
      && form.subject.trim()
      && form.body.trim()
      && (!form.attach_resume || form.resume_id),
  );
  const draftReadOnly = currentStatus === "sent" || !refinementAllowed;
  const canSave = !draftReadOnly && dirty && saveIsValid && busy === null;
  const refinementBlocked = draftReadOnly
    || review?.status === "sending"
    || review?.status === "uncertain"
    || review?.status === "sent";

  const loadLists = useCallback(async () => {
    setError(null);
    try {
      const [purposeData, resumeData, draftData, gmailData] = await Promise.all([
        apiRequest<Purpose[]>("/api/settings/purposes"),
        apiRequest<Resume[]>("/api/settings/resumes"),
        apiRequest<Draft[]>("/api/drafts"),
        apiRequest<GmailStatus>("/api/gmail/status"),
      ]);
      setPurposes(purposeData); setResumes(resumeData); setDrafts(draftData);
      setGmail(gmailData);
      setForm((previous) => ({
        ...previous,
        purpose_id: previous.purpose_id || purposeData.find((item) => item.is_default)?.id || purposeData[0]?.id || "",
        resume_id: previous.resume_id || resumeData.find((item) => item.is_default)?.id || "",
      }));
    } catch (loadError) { setError(errorMessage(loadError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void loadLists(); }, [loadLists]);
  useEffect(() => {
    if (loading || currentId) return;
    const requested = new URLSearchParams(window.location.search).get("draft");
    const draft = drafts.find((item) => item.id === requested);
    if (draft) {
      openDraft(draft);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [loading, drafts, currentId]);

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    if (draftReadOnly) return;
    setForm((previous) => ({ ...previous, [key]: value }));
    setDirty(true); setSuccess(null); setReview(null); setApproved(false); setRevision(null);
  }

  function contextPayload() {
    return {
      recipient_name: form.recipient_name, recipient_email: form.recipient_email,
      company: form.company || null, role: form.role || null,
      job_description: form.job_description || null, purpose_id: form.purpose_id,
      resume_id: form.resume_id || null,
      attach_resume: Boolean(form.resume_id && form.attach_resume),
    };
  }

  function openDraft(draft: Draft) {
    if (dirty && !window.confirm("Replace your unsaved edits with this saved draft?")) return;
    setCurrentId(draft.id);
    setCurrentStatus(draft.status);
    setCurrentConversationId(draft.conversation_id);
    setRefinementAllowed(draft.refinement_allowed);
    setForm({
      recipient_name: draft.recipient_name, recipient_email: draft.recipient_email,
      company: draft.company ?? "", role: draft.role ?? "",
      job_description: draft.job_description ?? "", purpose_id: draft.purpose_id,
      resume_id: draft.resume_id ?? "", attach_resume: draft.attach_resume && Boolean(draft.resume_id),
      subject: draft.subject, body: draft.body,
    });
    setDirty(false); setError(null); setSuccess(null); setReview(null); setApproved(false); setRevision(null); setRefinementInstruction("");
  }

  function newDraft() {
    if (dirty && !window.confirm("Discard your unsaved edits and start a new draft?")) return;
    setCurrentId(null);
    setCurrentStatus("not_sent");
    setCurrentConversationId(null);
    setRefinementAllowed(true);
    setForm({ ...emptyForm, purpose_id: purposes.find((item) => item.is_default)?.id || purposes[0]?.id || "", resume_id: resumes.find((item) => item.is_default)?.id || "" });
    setDirty(false); setError(null); setSuccess(null); setReview(null); setApproved(false); setRevision(null); setRefinementInstruction("");
  }

  async function generate(event: FormEvent) {
    event.preventDefault();
    if (busy || currentConversationId) return;
    if (currentId && dirty && form.subject && !window.confirm("Regenerate and replace your unsaved subject and body edits?")) return;
    setBusy("generate"); setError(null); setSuccess(null);
    try {
      const generated = await apiRequest<Draft>("/api/drafts/generate", {
        method: "POST", body: JSON.stringify({ ...contextPayload(), draft_id: currentId }),
      });
      setCurrentId(generated.id);
      setCurrentStatus("not_sent");
      setCurrentConversationId(generated.conversation_id);
      setRefinementAllowed(generated.refinement_allowed);
      setForm((previous) => ({ ...previous, subject: generated.subject, body: generated.body }));
      setDirty(false); setReview(null); setApproved(false); setRevision(null); setRefinementInstruction(""); await loadLists();
      setSuccess(currentId ? "Draft regenerated and saved." : "Draft generated and saved.");
    } catch (generationError) { setError(errorMessage(generationError)); }
    finally { setBusy(null); }
  }

  async function save() {
    if (!currentId || busy) return;
    setBusy("save"); setError(null);
    try {
      const saved = await apiRequest<Draft>(`/api/drafts/${currentId}`, {
        method: "PUT", body: JSON.stringify({ ...contextPayload(), subject: form.subject, body: form.body }),
      });
      setForm((previous) => ({ ...previous, subject: saved.subject, body: saved.body }));
      setDirty(false); await loadLists(); setSuccess("Draft saved. It has not been sent.");
    } catch (saveError) { setError(errorMessage(saveError)); }
    finally { setBusy(null); }
  }

  async function refine() {
    if (!currentId || busy || refinementBlocked || !refinementInstruction.trim()) return;
    setBusy("refine"); setError(null); setSuccess(null); setRevision(null);
    try {
      const proposed = await apiRequest<EmailRevision>(`/api/drafts/${currentId}/refine`, {
        method: "POST",
        body: JSON.stringify({
          ...contextPayload(), subject: form.subject, body: form.body,
          instruction: refinementInstruction,
        }),
      });
      setRevision(proposed);
      setSuccess("AI revision ready. Compare it with your current draft before accepting.");
    } catch (refinementError) { setError(errorMessage(refinementError)); }
    finally { setBusy(null); }
  }

  function acceptRevision() {
    if (!revision || currentStatus === "sent") return;
    setForm((previous) => ({ ...previous, subject: revision.subject, body: revision.body }));
    setDirty(true); setRevision(null); setRefinementInstruction("");
    setReview(null); setApproved(false); setError(null);
    setSuccess("AI revision applied as unsaved edits. Save the draft when ready.");
  }

  function discardRevision() {
    setRevision(null); setSuccess("AI revision discarded. Your current draft was not changed.");
  }

  async function removeDraft(draftId: string) {
    if (busy || !window.confirm("Remove this draft from RecruitFlow? It will be permanently hidden in RecruitFlow, but no email will be deleted or changed in Gmail.")) return;
    setBusy("delete"); setDeletingId(draftId); setError(null); setSuccess(null);
    try {
      await apiRequest(`/api/drafts/${draftId}`, { method: "DELETE" });
      setDrafts((previous) => previous.filter((draft) => draft.id !== draftId));
      if (currentId === draftId) {
        setCurrentId(null); setCurrentStatus("not_sent"); setCurrentConversationId(null); setRefinementAllowed(true); setReview(null); setApproved(false); setRevision(null); setRefinementInstruction("");
        setForm({ ...emptyForm, purpose_id: purposes.find((item) => item.is_default)?.id || purposes[0]?.id || "", resume_id: resumes.find((item) => item.is_default)?.id || "" }); setDirty(false);
      }
      setSuccess("Draft removed from RecruitFlow. Gmail was not changed.");
    } catch (deleteError) { setError(errorMessage(deleteError)); }
    finally { setBusy(null); setDeletingId(null); }
  }

  async function prepareReview() {
    if (!currentId || dirty || busy || currentStatus === "sent") return;
    setBusy("review"); setError(null); setSuccess(null); setApproved(false);
    try {
      const prepared = await apiRequest<SendReview>(`/api/gmail/reviews/${currentId}`, { method: "POST" });
      setReview(prepared);
      setSuccess("Review prepared. Confirm every detail before sending.");
    } catch (reviewError) { setError(errorMessage(reviewError)); }
    finally { setBusy(null); }
  }

  async function sendApproved() {
    if (!review || !approved || busy) return;
    setBusy("send"); setError(null); setSuccess(null);
    try {
      const result = await apiRequest<SendResult>(`/api/gmail/sends/${review.id}`, {
        method: "POST", body: JSON.stringify({ approved: true }),
      });
      if (result.status === "uncertain") {
        setRefinementAllowed(false);
        setReview({ ...review, status: "uncertain" });
        setError("Gmail did not confirm the result. Do not resend; check Gmail Sent mail manually.");
      } else {
        setCurrentStatus("sent"); setRefinementAllowed(false); setReview({ ...review, status: "sent" });
        setSuccess("Email sent and confirmed by Gmail.");
      }
      setApproved(false); await loadLists();
    } catch (sendError) { setError(errorMessage(sendError)); }
    finally { setBusy(null); }
  }

  if (loading) return <div className="card p-8 text-slate-600" role="status">Loading compose workspace…</div>;

  return (
    <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="card h-fit p-4" aria-label="Saved drafts">
        <div className="flex items-center justify-between gap-3"><h2 className="font-bold text-slate-950">Drafts</h2><button type="button" className="button-secondary min-h-9 px-3 py-1" onClick={newDraft}>New</button></div>
        <p className="mt-1 text-sm text-slate-500">Saved RecruitFlow drafts</p>
        <div className="mt-4 space-y-2">
          {drafts.length === 0 ? <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">No saved drafts yet.</p> : drafts.map((draft) => (
            <div key={draft.id} className={`flex items-stretch rounded-lg border ${currentId === draft.id ? "border-blue-300 bg-blue-50" : "border-slate-200"}`}>
              <button type="button" onClick={() => openDraft(draft)} disabled={busy !== null} className="min-w-0 flex-1 rounded-l-lg p-3 text-left hover:bg-slate-50 disabled:cursor-not-allowed">
                <span className="block truncate text-sm font-semibold text-slate-900">{draft.subject}</span>
                <span className="mt-1 block truncate text-xs text-slate-500">{draft.recipient_name} · {draft.status === "sent" ? "Sent" : "Not sent"}</span>
              </button>
              <button type="button" onClick={() => void removeDraft(draft.id)} disabled={busy !== null} className="rounded-r-lg border-l border-slate-200 px-3 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60" aria-label={`Delete draft: ${draft.subject}`}>{deletingId === draft.id ? "Deleting…" : "Delete"}</button>
            </div>
          ))}
        </div>
      </aside>

      <div className="space-y-5">
        {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
        {success && <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">{success}</div>}
        {purposes.length === 0 ? (
          <section className="card p-8 text-center"><h2 className="text-lg font-bold">Add an email purpose first</h2><p className="mt-2 text-slate-600">Compose needs reusable instructions to ground the draft.</p><Link href="/settings" className="button-primary mt-5">Open Settings</Link></section>
        ) : (
          <form onSubmit={generate} className="card p-5 sm:p-7">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-bold">Email context</h2><p className="mt-1 text-sm text-slate-500">Only these details are provided to the configured AI model.</p></div>{currentId && <span className={`rounded-full px-3 py-1.5 text-sm font-bold ${currentStatus === "sent" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{currentStatus === "sent" ? "Sent" : "Not sent"}</span>}</div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Recipient name" id="recipient-name"><input id="recipient-name" className="field" value={form.recipient_name} onChange={(e) => update("recipient_name", e.target.value)} maxLength={160} disabled={draftReadOnly} required /></Field>
              <Field label="Recipient email" id="recipient-email"><input id="recipient-email" type="email" className="field" value={form.recipient_email} onChange={(e) => update("recipient_email", e.target.value)} maxLength={320} disabled={draftReadOnly || Boolean(currentConversationId)} required /></Field>
              <Field label="Company (optional)" id="company"><input id="company" className="field" value={form.company} onChange={(e) => update("company", e.target.value)} maxLength={160} disabled={draftReadOnly} /></Field>
              <Field label="Job title (optional)" id="role"><input id="role" className="field" value={form.role} onChange={(e) => update("role", e.target.value)} maxLength={160} disabled={draftReadOnly} /></Field>
              <Field label="Email purpose" id="purpose"><select id="purpose" className="field" value={form.purpose_id} onChange={(e) => update("purpose_id", e.target.value)} disabled={draftReadOnly} required>{purposes.map((purpose) => <option key={purpose.id} value={purpose.id}>{purpose.title}{purpose.is_default ? " (default)" : ""}</option>)}</select></Field>
              <Field label="Resume context" id="resume"><select id="resume" className="field" value={form.resume_id} onChange={(e) => { update("resume_id", e.target.value); if (!e.target.value) update("attach_resume", false); }} disabled={draftReadOnly}><option value="">No resume</option>{resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.display_name}{resume.is_default ? " (default)" : ""}</option>)}</select></Field>
              <div className="sm:col-span-2"><label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm"><input type="checkbox" className="mt-1 size-4" checked={form.attach_resume} disabled={!form.resume_id || draftReadOnly} onChange={(e) => update("attach_resume", e.target.checked)} /><span><span className="font-semibold text-slate-800">Attach selected resume</span><span className="block text-slate-500">The private PDF is attached only after final review and explicit approval.</span></span></label></div>
              <div className="sm:col-span-2"><Field label="Job description (optional)" id="job-description"><textarea id="job-description" className="field min-h-36 resize-y" value={form.job_description} onChange={(e) => update("job_description", e.target.value)} maxLength={12000} disabled={draftReadOnly} /></Field></div>
            </div>
            {!currentConversationId && <button className="button-primary mt-5" disabled={busy !== null || draftReadOnly}>{busy === "generate" ? "Generating…" : currentId ? "Regenerate" : "Generate email"}</button>}
          </form>
        )}

        {(currentId || form.subject || form.body) && (
          <section className="card p-5 sm:p-7" aria-labelledby="draft-editor-heading">
            <div className="mb-5"><h2 id="draft-editor-heading" className="text-lg font-bold">Draft editor</h2><p className="mt-1 text-sm text-slate-500">{currentStatus === "sent" ? "This sent draft is read-only." : currentConversationId ? "Follow-up draft · The recipient and thread subject are locked to the original Gmail conversation." : "Review and save every word before preparing the final send review."}</p></div>
            <div className="space-y-4"><Field label="Subject" id="subject"><input id="subject" className="field" value={form.subject} onChange={(e) => update("subject", e.target.value)} maxLength={200} disabled={draftReadOnly || Boolean(currentConversationId)} required /></Field><Field label="Body" id="body"><textarea id="body" className="field min-h-80 resize-y leading-7" value={form.body} onChange={(e) => update("body", e.target.value)} maxLength={10000} disabled={draftReadOnly} required /></Field></div>
            <div className="mt-5 flex flex-wrap items-center gap-2"><button type="button" className="button-primary" onClick={() => void save()} disabled={!canSave} aria-describedby="save-draft-status">{busy === "save" ? "Saving…" : "Save draft"}</button><button type="button" className="button-danger" onClick={() => currentId && void removeDraft(currentId)} disabled={!currentId || busy !== null}>{busy === "delete" && deletingId === currentId ? "Deleting…" : "Delete draft"}</button><span id="save-draft-status" role="status" className={`text-sm font-medium ${dirty ? "text-amber-700" : "text-emerald-700"}`}>{currentStatus === "sent" ? "Sent · Read-only" : !refinementAllowed ? "Active or uncertain send · Read-only" : !currentId ? "Generate the email to create a saved draft." : dirty ? saveIsValid ? "Unsaved changes · Not sent" : "Complete the required fields to save · Not sent" : "Saved · Not sent — edit any field to enable Save draft."}</span></div>
            <div className="mt-6 border-t border-slate-200 pt-6">
              <label htmlFor="refinement-instruction" className="block text-sm font-semibold text-slate-800">Ask AI to refine this email</label>
              <textarea id="refinement-instruction" className="field mt-2 min-h-24 resize-y" value={refinementInstruction} onChange={(event) => setRefinementInstruction(event.target.value)} placeholder="e.g. Make it shorter and rewrite the opening." maxLength={1000} disabled={!currentId || refinementBlocked || busy === "refine"} />
              <p className="mt-2 text-xs text-slate-500">The current subject and body, including unsaved edits, are sent to the configured AI provider. Nothing is overwritten or sent automatically.</p>
              <button type="button" className="button-secondary mt-3" onClick={() => void refine()} disabled={!currentId || refinementBlocked || busy !== null || !refinementInstruction.trim() || !form.subject.trim() || !form.body.trim()}>{busy === "refine" ? "Refining…" : "Refine with AI"}</button>
            </div>
            {revision && (
              <div className="mt-6 rounded-xl border border-blue-200 bg-blue-50 p-4" aria-live="polite">
                <h3 className="font-bold text-blue-950">Proposed AI revision</h3>
                <dl className="mt-4 space-y-4 text-sm"><div><dt className="font-semibold text-blue-900">Subject</dt><dd className="mt-1 text-slate-900">{revision.subject}</dd></div><div><dt className="font-semibold text-blue-900">Body</dt><dd className="mt-1 whitespace-pre-wrap leading-7 text-slate-900">{revision.body}</dd></div></dl>
                <div className="mt-4 flex flex-wrap gap-2"><button type="button" className="button-primary" onClick={acceptRevision} disabled={busy !== null}>Accept revision</button><button type="button" className="button-secondary" onClick={discardRevision} disabled={busy !== null}>Discard</button></div>
              </div>
            )}
          </section>
        )}

        {currentId && currentStatus === "not_sent" && (
          <section className="card p-5 sm:p-7" aria-labelledby="send-review-heading">
            <h2 id="send-review-heading" className="text-lg font-bold">Final send review</h2>
            {currentConversationId && <p className="mt-2 rounded-lg bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-800">This follow-up will be sent in its original Gmail conversation.</p>}
            {!gmail.connected ? <p className="mt-2 text-sm text-slate-600">Connect Gmail in <Link href="/settings" className="font-semibold text-blue-700 underline">Settings</Link> before preparing a send review.</p> : dirty ? <p className="mt-2 text-sm text-amber-700">Save your changes before preparing the final review.</p> : !review ? <><p className="mt-2 text-sm text-slate-600">Preparing a review does not send anything. You must approve the reviewed snapshot separately.</p><button type="button" className="button-primary mt-4" onClick={() => void prepareReview()} disabled={busy !== null}>{busy === "review" ? "Preparing…" : "Prepare final review"}</button></> : (
              <div className="mt-5 space-y-4">
                <dl className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm sm:grid-cols-[8rem_1fr]"><dt className="font-semibold">From</dt><dd>{review.sender_email}</dd><dt className="font-semibold">To</dt><dd>{review.recipient_email}</dd><dt className="font-semibold">Subject</dt><dd>{review.subject}</dd><dt className="font-semibold">Attachment</dt><dd>{review.attachment ? `${review.attachment.filename} (${formatBytes(review.attachment.size_bytes)})` : "No attachment"}</dd></dl>
                <div><p className="mb-2 text-sm font-semibold text-slate-700">Plain-text body</p><div className="whitespace-pre-wrap rounded-xl border border-slate-200 bg-white p-4 text-sm leading-7 text-slate-800">{review.body}</div></div>
                {review.status === "pending" ? <><label className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm"><input type="checkbox" className="mt-1 size-4" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><span className="font-semibold text-amber-950">I approve this exact email and attachment for sending now.</span><span className="mt-1 block text-amber-800">Every send requires this explicit approval. Editing the draft invalidates this review.</span></span></label><button type="button" className="button-primary" onClick={() => void sendApproved()} disabled={!approved || busy !== null}>{busy === "send" ? "Sending…" : "Approve and send with Gmail"}</button></> : <p className={`rounded-xl p-4 text-sm font-semibold ${review.status === "sent" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}>{review.status === "sent" ? "Sent and confirmed by Gmail." : "Delivery is uncertain. Do not resend; check Gmail Sent mail manually."}</p>}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return <div><label htmlFor={id} className="mb-2 block text-sm font-semibold text-slate-700">{label}</label>{children}</div>;
}
