import { api } from "@/lib/api";
import type { PopulationDistribution, PopulationPortrait } from "@/lib/api";
import { PieChart } from "lucide-react";

const EMPTY: PopulationPortrait = { total_users: 0, distributions: [] };

// 分型标签配色，与单人画像页顶部标签保持一致。
const CUSTOMER_TYPE_COLORS: Record<string, string> = {
  疾病管理型: "bg-rose-500",
  减重攻坚型: "bg-amber-500",
  健康投资型: "bg-emerald-500",
};

function barColor(distKey: string, label: string, index: number): string {
  if (distKey === "customer_type" && CUSTOMER_TYPE_COLORS[label]) {
    return CUSTOMER_TYPE_COLORS[label];
  }
  const palette = ["bg-cyan-500", "bg-sky-500", "bg-indigo-500", "bg-violet-500", "bg-fuchsia-500"];
  return palette[index % palette.length];
}

function DistributionPanel({ dist }: { dist: PopulationDistribution }) {
  const denom = dist.total + dist.unknown; // 展示占比的分母含未采集，避免误导
  const maxCount = Math.max(1, ...dist.buckets.map((b) => b.count));
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div>
          <h2 className="panel-title">{dist.label}</h2>
          <p className="panel-subtitle">{dist.description}</p>
        </div>
        <span className="status-pill pill-neutral">
          有效 {dist.total}
          {dist.unknown > 0 ? ` · 未采集 ${dist.unknown}` : ""}
        </span>
      </div>
      <div className="panel-body space-y-3">
        {dist.buckets.length === 0 ? (
          <div className="empty-state">暂无有效样本</div>
        ) : (
          dist.buckets.map((bucket, index) => {
            const pct = denom > 0 ? Math.round((bucket.count * 100) / denom) : 0;
            const widthPct = Math.round((bucket.count * 100) / maxCount);
            return (
              <div key={bucket.label} className="flex items-center gap-3">
                <div className="w-32 shrink-0 truncate text-sm text-slate-700" title={bucket.label}>
                  {bucket.label}
                </div>
                <div className="relative h-6 flex-1 overflow-hidden rounded-md bg-slate-100">
                  <div
                    className={`h-full rounded-md ${barColor(dist.key, bucket.label, index)}`}
                    style={{ width: `${Math.max(widthPct, bucket.count > 0 ? 4 : 0)}%` }}
                  />
                </div>
                <div className="w-24 shrink-0 text-right text-sm tabular-nums text-slate-600">
                  {bucket.count} 人 · {pct}%
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

export default async function UserSegmentsPage() {
  const portrait = await api<PopulationPortrait>("/dashboard/population-portrait").catch(() => EMPTY);

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-50 text-cyan-600">
              <PieChart className="h-5 w-5" />
            </span>
            <div>
              <h1 className="text-xl font-bold text-slate-900">群体画像</h1>
              <p className="text-sm text-slate-500">
                基于 {portrait.total_users} 位已建档在服务用户的量化分布，随数据积累实时变化。
              </p>
            </div>
          </div>
        </div>
      </div>

      {portrait.total_users === 0 ? (
        <div className="empty-state">暂无已建档用户，群体画像将在建档后自动生成。</div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {portrait.distributions.map((dist) => (
            <DistributionPanel key={dist.key} dist={dist} />
          ))}
        </div>
      )}

      <p className="text-xs text-slate-400">
        说明：客户分型按“疾病管理 &gt; 减重攻坚 &gt; 健康投资”优先级由风险等级与减重带确定性推导；体脂率、腰围为选填，
        未采集用户单独计数，占比以全体已建档用户为分母。饮食 / 睡眠 / 地域等习惯维度将于后续版本接入。
      </p>
    </div>
  );
}
