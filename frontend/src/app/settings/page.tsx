import { SettingsDashboard } from "@/components/settings-dashboard";

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-8">
        <p className="eyebrow">Settings</p>
        <h1 className="page-title">Personalize your workspace</h1>
        <p className="page-description">
          Manage the sender name, resumes, and reusable context RecruitFlow will use in future drafts.
        </p>
      </div>
      <SettingsDashboard />
    </div>
  );
}

