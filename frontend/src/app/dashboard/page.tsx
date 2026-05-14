import { api } from "@/lib/api";

type Summary = {
  service_cases_total: number;
  service_cases_in_service: number;
  checkins_today: number;
  messages_today: number;
  pending_documents: number;
};

export default async function DashboardPage() {
  const summary = await api<Summary>("/dashboard/summary").catch(() => ({
    service_cases_total: 0,
    service_cases_in_service: 0,
    checkins_today: 0,
    messages_today: 0,
    pending_documents: 0,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">基础看板</h1>
        <p className="mt-1 text-sm text-zinc-500">服务、打卡、消息与待审核文件概览。</p>
      </div>
      <div className="grid gap-4 md:grid-cols-5">
        <Metric label="服务单总数" value={summary.service_cases_total} />
        <Metric label="服务中" value={summary.service_cases_in_service} />
        <Metric label="今日打卡" value={summary.checkins_today} />
        <Metric label="今日消息" value={summary.messages_today} />
        <Metric label="待审核文件" value={summary.pending_documents} />
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="text-sm text-zinc-500">{label}</div>
      <div className="mt-2 text-3xl font-semibold">{value}</div>
    </div>
  );
}
