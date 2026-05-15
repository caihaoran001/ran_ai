import Link from "next/link";
import { api, Checkin } from "@/lib/api";

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

  const checkins = await api<Checkin[]>("/checkins").catch(() => []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">数据看板</h1>
        <p className="mt-1 text-sm text-zinc-500">服务概览与关键指标</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="服务单总数"
          value={summary.service_cases_total}
          icon="📋"
          color="blue"
        />
        <StatCard
          label="服务中"
          value={summary.service_cases_in_service}
          icon="🟢"
          color="green"
        />
        <StatCard
          label="今日打卡"
          value={summary.checkins_today}
          icon="✅"
          color="yellow"
        />
        <StatCard
          label="今日消息"
          value={summary.messages_today}
          icon="💬"
          color="purple"
        />
        <StatCard
          label="待审核文件"
          value={summary.pending_documents}
          icon="📄"
          color="orange"
        />
      </div>

      <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-zinc-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-zinc-900">最近打卡</h2>
          <Link href="/checkins" className="text-sm text-blue-600 hover:text-blue-700">
            查看全部 →
          </Link>
        </div>
        <div className="p-6">
          {checkins.length > 0 ? (
            <div className="space-y-3">
              {checkins.slice(0, 10).map((checkin) => (
                <div
                  key={checkin.id}
                  className="flex items-center justify-between rounded-lg border border-zinc-100 p-4 transition-colors hover:border-zinc-200 hover:bg-zinc-50"
                >
                  <div className="flex items-center gap-4">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-green-100 text-green-700">
                      ✓
                    </div>
                    <div>
                      <div className="font-medium text-zinc-900">{checkin.content}</div>
                      <div className="text-sm text-zinc-500">{checkin.checkin_date}</div>
                    </div>
                  </div>
                  <div className="text-sm text-zinc-400">
                    {formatTime(checkin.created_at)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-12 text-center text-zinc-400">
              <div className="mb-2 text-4xl">📋</div>
              <p>暂无打卡记录</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  color,
}: {
  label: string;
  value: number;
  icon: string;
  color: string;
}) {
  const colorClasses: Record<string, string> = {
    blue: "bg-blue-50 border-blue-200",
    green: "bg-green-50 border-green-200",
    yellow: "bg-yellow-50 border-yellow-200",
    purple: "bg-purple-50 border-purple-200",
    orange: "bg-orange-50 border-orange-200",
  };

  return (
    <div className={`rounded-xl border p-6 ${colorClasses[color] || "bg-zinc-50 border-zinc-200"}`}>
      <div className="flex items-center justify-between">
        <span className="text-2xl">{icon}</span>
        <span className="text-3xl font-bold text-zinc-900">{value}</span>
      </div>
      <div className="mt-2 text-sm font-medium text-zinc-600">{label}</div>
    </div>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
