import { HistoryDashboard } from "@/components/history-dashboard";

export default function HistoryPage() {
  return <div className="mx-auto max-w-7xl"><div className="mb-8"><p className="eyebrow">History</p><h1 className="page-title">Email activity</h1><p className="page-description">Review messages sent through RecruitFlow and prepare human-approved follow-ups.</p></div><HistoryDashboard /></div>;
}
