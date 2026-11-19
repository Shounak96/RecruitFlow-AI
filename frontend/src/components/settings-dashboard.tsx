"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, apiUrl } from "@/lib/api";

type Profile = { name: string; gmail_connected: boolean; gmail_email: string | null };
type GmailAuthorization = { authorization_url: string };
type Resume = {
  id: string;
  display_name: string;
  original_filename: string;
  size_bytes: number;
  created_at: string;
  is_default: boolean;
};
type Purpose = {
  id: string;
  title: string;
  description: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SettingsDashboard() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [purposes, setPurposes] = useState<Purpose[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [senderName, setSenderName] = useState("");
  const [resumeName, setResumeName] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [purposeTitle, setPurposeTitle] = useState("");
  const [purposeDescription, setPurposeDescription] = useState("");
  const [editingPurpose, setEditingPurpose] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [profileData, resumeData, purposeData] = await Promise.all([
        apiRequest<Profile>("/api/settings/profile"),
        apiRequest<Resume[]>("/api/settings/resumes"),
        apiRequest<Purpose[]>("/api/settings/purposes"),
      ]);
      setProfile(profileData);
      setSenderName(profileData.name);
      setResumes(resumeData);
      setPurposes(purposeData);
    } catch (loadError) {
      setError(messageFrom(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("gmail");
    if (!result) return;
    const messages: Record<string, string> = {
      connected: "Gmail connected and verified.",
      denied: "Gmail connection was cancelled.",
      invalid_state: "The Gmail connection request expired or was invalid. Try again.",
      failed: "Google could not complete the Gmail connection.",
      missing_refresh: "Google did not provide offline access. Disconnect access in your Google account, then try again.",
    };
    if (result === "connected") setSuccess(messages[result]);
    else setError(messages[result] ?? "Gmail connection was not completed.");
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  function notify(message: string) {
    setError(null);
    setSuccess(message);
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setBusy("profile");
    setSuccess(null);
    try {
      const saved = await apiRequest<Profile>("/api/settings/profile", {
        method: "PUT",
        body: JSON.stringify({ name: senderName }),
      });
      setProfile(saved);
      setSenderName(saved.name);
      notify("Sender profile saved.");
    } catch (saveError) {
      setError(messageFrom(saveError));
    } finally {
      setBusy(null);
    }
  }

  async function connectGmail() {
    setBusy("gmail"); setError(null); setSuccess(null);
    try {
      const result = await apiRequest<GmailAuthorization>("/api/gmail/oauth/authorize");
      window.location.assign(result.authorization_url);
    } catch (connectError) {
      setError(messageFrom(connectError));
      setBusy(null);
    }
  }

  async function disconnectGmail() {
    if (!window.confirm("Disconnect Gmail and revoke RecruitFlow's sending access?")) return;
    setBusy("gmail"); setError(null); setSuccess(null);
    try {
      await apiRequest("/api/gmail/connection", { method: "DELETE" });
      await load();
      notify("Gmail disconnected.");
    } catch (disconnectError) { setError(messageFrom(disconnectError)); }
    finally { setBusy(null); }
  }

  async function uploadResume(event: FormEvent) {
    event.preventDefault();
    if (!resumeFile) { setError("Choose a PDF resume to upload."); return; }
    const formData = new FormData();
    formData.set("display_name", resumeName);
    formData.set("file", resumeFile);
    setBusy("upload");
    setSuccess(null);
    try {
      await apiRequest<Resume>("/api/settings/resumes", { method: "POST", body: formData });
      setResumeName("");
      setResumeFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await load();
      notify("Resume uploaded securely.");
    } catch (uploadError) {
      setError(messageFrom(uploadError));
    } finally {
      setBusy(null);
    }
  }

  async function setDefaultResume(id: string) {
    setBusy(`resume-${id}`);
    try {
      await apiRequest(`/api/settings/resumes/${id}/default`, { method: "PUT" });
      await load();
      notify("Default resume updated.");
    } catch (actionError) { setError(messageFrom(actionError)); }
    finally { setBusy(null); }
  }

  async function deleteResume(resume: Resume) {
    if (!window.confirm(`Delete “${resume.display_name}”? This cannot be undone.`)) return;
    setBusy(`resume-${resume.id}`);
    try {
      await apiRequest(`/api/settings/resumes/${resume.id}`, { method: "DELETE" });
      await load();
      notify("Resume deleted.");
    } catch (actionError) { setError(messageFrom(actionError)); }
    finally { setBusy(null); }
  }

  function resetPurposeForm() {
    setEditingPurpose(null);
    setPurposeTitle("");
    setPurposeDescription("");
  }

  function startEditing(purpose: Purpose) {
    setEditingPurpose(purpose.id);
    setPurposeTitle(purpose.title);
    setPurposeDescription(purpose.description);
    setSuccess(null);
  }

  async function savePurpose(event: FormEvent) {
    event.preventDefault();
    setBusy("purpose-form");
    setSuccess(null);
    try {
      const path = editingPurpose
        ? `/api/settings/purposes/${editingPurpose}`
        : "/api/settings/purposes";
      await apiRequest(path, {
        method: editingPurpose ? "PUT" : "POST",
        body: JSON.stringify({ title: purposeTitle, description: purposeDescription }),
      });
      const message = editingPurpose ? "Email purpose updated." : "Email purpose created.";
      resetPurposeForm();
      await load();
      notify(message);
    } catch (saveError) { setError(messageFrom(saveError)); }
    finally { setBusy(null); }
  }

  async function setDefaultPurpose(id: string) {
    setBusy(`purpose-${id}`);
    try {
      await apiRequest(`/api/settings/purposes/${id}/default`, { method: "PUT" });
      await load();
      notify("Default email purpose updated.");
    } catch (actionError) { setError(messageFrom(actionError)); }
    finally { setBusy(null); }
  }

  async function deletePurpose(purpose: Purpose) {
    if (!window.confirm(`Delete “${purpose.title}”? This cannot be undone.`)) return;
    setBusy(`purpose-${purpose.id}`);
    try {
      await apiRequest(`/api/settings/purposes/${purpose.id}`, { method: "DELETE" });
      if (editingPurpose === purpose.id) resetPurposeForm();
      await load();
      notify("Email purpose deleted.");
    } catch (actionError) { setError(messageFrom(actionError)); }
    finally { setBusy(null); }
  }

  if (loading) {
    return <div className="card p-8 text-slate-600" role="status">Loading your settings…</div>;
  }

  return (
    <div className="space-y-6">
      {error && (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error} <button type="button" className="ml-2 font-bold underline" onClick={() => void load()}>Try again</button>
        </div>
      )}
      {success && (
        <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
          {success}
        </div>
      )}

      <section className="card p-5 sm:p-7" aria-labelledby="sender-heading">
        <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
          <div>
            <h2 id="sender-heading" className="text-lg font-bold text-slate-950">Sender profile</h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">Used to personalize future email drafts.</p>
          </div>
          <span className={`inline-flex w-fit items-center gap-2 rounded-full px-3 py-1.5 text-sm font-semibold ${profile?.gmail_connected ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-200"}`}>
            <span className={`size-2 rounded-full ${profile?.gmail_connected ? "bg-emerald-600 dark:bg-emerald-400" : "bg-slate-400 dark:bg-slate-500"}`} aria-hidden="true" />
            Gmail: {profile?.gmail_connected ? "Connected" : "Not connected"}
          </span>
        </div>
        <form onSubmit={saveProfile} className="flex max-w-2xl flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label htmlFor="sender-name" className="mb-2 block text-sm font-semibold text-slate-700">Sender name</label>
            <input id="sender-name" className="field" value={senderName} onChange={(e) => setSenderName(e.target.value)} maxLength={120} required />
          </div>
          <button className="button-primary" disabled={busy === "profile"}>{busy === "profile" ? "Saving…" : "Save profile"}</button>
        </form>
        <div className="mt-5 flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-semibold text-slate-900">Verified Gmail identity</p>
            <p className="mt-1 text-sm text-slate-600">{profile?.gmail_connected ? profile.gmail_email : "Not connected"}</p>
            <p className="mt-1 text-xs text-slate-500">RecruitFlow requests identity, Gmail send, and read-only access for manual reply sync. A typed sender address is never treated as verified.</p>
          </div>
          {profile?.gmail_connected ? (
            <button type="button" className="button-danger shrink-0" disabled={busy === "gmail"} onClick={() => void disconnectGmail()}>{busy === "gmail" ? "Disconnecting…" : "Disconnect Gmail"}</button>
          ) : (
            <button type="button" className="button-primary shrink-0" disabled={busy === "gmail"} onClick={() => void connectGmail()}>{busy === "gmail" ? "Connecting…" : "Connect Gmail"}</button>
          )}
        </div>
      </section>

      <section className="card p-5 sm:p-7" aria-labelledby="resumes-heading">
        <div className="mb-6">
          <h2 id="resumes-heading" className="text-lg font-bold text-slate-950">Resumes</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">Upload private PDF files up to 5 MB. The default is preselected in future drafts.</p>
        </div>
        <form onSubmit={uploadResume} className="grid gap-4 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2">
          <div>
            <label htmlFor="resume-name" className="mb-2 block text-sm font-semibold text-slate-700">Display name</label>
            <input id="resume-name" className="field" value={resumeName} onChange={(e) => setResumeName(e.target.value)} placeholder="e.g. Product engineering resume" maxLength={160} required />
          </div>
          <div>
            <label htmlFor="resume-file" className="mb-2 block text-sm font-semibold text-slate-700">PDF file</label>
            <input ref={fileInput} id="resume-file" type="file" accept="application/pdf,.pdf" className="field file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-semibold" onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)} required />
          </div>
          <div className="sm:col-span-2">
            <button className="button-primary" disabled={busy === "upload"}>{busy === "upload" ? "Uploading…" : "Upload resume"}</button>
          </div>
        </form>

        <div className="mt-6 space-y-3">
          {resumes.length === 0 ? (
            <p className="rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-slate-500">No resumes yet. Upload your first PDF above.</p>
          ) : resumes.map((resume) => (
            <article key={resume.id} className="flex flex-col gap-4 rounded-xl border border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate font-semibold text-slate-900">{resume.display_name}</h3>
                  {resume.is_default && <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-700">Default</span>}
                </div>
                <p className="mt-1 truncate text-sm text-slate-500">{resume.original_filename} · {formatBytes(resume.size_bytes)}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <a className="button-secondary" href={apiUrl(`/api/settings/resumes/${resume.id}/download`)}>Download</a>
                {!resume.is_default && <button type="button" className="button-secondary" disabled={busy === `resume-${resume.id}`} onClick={() => void setDefaultResume(resume.id)}>Set default</button>}
                <button type="button" className="button-danger" disabled={busy === `resume-${resume.id}`} onClick={() => void deleteResume(resume)}>Delete</button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="card p-5 sm:p-7" aria-labelledby="purposes-heading">
        <div className="mb-6">
          <h2 id="purposes-heading" className="text-lg font-bold text-slate-950">Email purposes</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">Reusable instructions that will guide future email drafts.</p>
        </div>
        <form onSubmit={savePurpose} className="rounded-xl border border-slate-200 bg-slate-50 p-4">
          <h3 className="mb-4 font-semibold text-slate-900">{editingPurpose ? "Edit purpose" : "Add a purpose"}</h3>
          <div className="grid gap-4">
            <div>
              <label htmlFor="purpose-title" className="mb-2 block text-sm font-semibold text-slate-700">Title</label>
              <input id="purpose-title" className="field" value={purposeTitle} onChange={(e) => setPurposeTitle(e.target.value)} placeholder="e.g. Introduce myself to a recruiter" maxLength={120} required />
            </div>
            <div>
              <label htmlFor="purpose-description" className="mb-2 block text-sm font-semibold text-slate-700">Description</label>
              <textarea id="purpose-description" className="field min-h-28 resize-y" value={purposeDescription} onChange={(e) => setPurposeDescription(e.target.value)} placeholder="Describe the tone, goal, and key points to include." maxLength={5000} required />
            </div>
            <div className="flex gap-2">
              <button className="button-primary" disabled={busy === "purpose-form"}>{busy === "purpose-form" ? "Saving…" : editingPurpose ? "Save changes" : "Add purpose"}</button>
              {editingPurpose && <button type="button" className="button-secondary" onClick={resetPurposeForm}>Cancel</button>}
            </div>
          </div>
        </form>

        <div className="mt-6 space-y-3">
          {purposes.length === 0 ? (
            <p className="rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-slate-500">No email purposes yet. Add one to reuse later.</p>
          ) : purposes.map((purpose) => (
            <article key={purpose.id} className="rounded-xl border border-slate-200 p-4">
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold text-slate-900">{purpose.title}</h3>
                    {purpose.is_default && <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-700">Default</span>}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600">{purpose.description}</p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <button type="button" className="button-secondary" onClick={() => startEditing(purpose)}>Edit</button>
                  {!purpose.is_default && <button type="button" className="button-secondary" disabled={busy === `purpose-${purpose.id}`} onClick={() => void setDefaultPurpose(purpose.id)}>Set default</button>}
                  <button type="button" className="button-danger" disabled={busy === `purpose-${purpose.id}`} onClick={() => void deletePurpose(purpose)}>Delete</button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

