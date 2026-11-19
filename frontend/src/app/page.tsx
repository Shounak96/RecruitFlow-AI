import { ComposeDashboard } from "@/components/compose-dashboard";

export default function Home() {
  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-8">
        <p className="eyebrow">Compose</p>
        <h1 className="page-title">Create a recruiter email</h1>
        <p className="page-description">Generate, refine, review, and explicitly approve recruiter emails and follow-ups.</p>
      </div>
      <ComposeDashboard />
    </div>
  );
}

