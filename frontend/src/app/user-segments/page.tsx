import { api } from "@/lib/api";
import type { PopulationPortrait } from "@/lib/api";
import { PieChart } from "lucide-react";
import { DistributionPanel } from "./distribution-panel";
import { GeographyPanel } from "./geography-panel";

const EMPTY: PopulationPortrait = { total_users: 0, distributions: [], geography: null };

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
          {portrait.geography && (
            <div className="lg:col-span-2">
              <GeographyPanel geography={portrait.geography} />
            </div>
          )}
          {portrait.distributions.map((dist) => (
            <DistributionPanel key={dist.key} dist={dist} />
          ))}
        </div>
      )}

      <p className="text-xs text-slate-400">
        说明：客户分型按“疾病管理 &gt; 减重攻坚 &gt; 健康投资”优先级由风险等级与减重带确定性推导；体脂率、腰围为选填，
        未采集用户单独计数，占比以全体已建档用户为分母。健康领域、具体健康情况、用药、特殊阶段和过敏史为多选维度，
        每个分桶按服务单去重，因此分桶人数之和可以超过总人数。地域按入营问卷填报城市归一为省→市层级；点击省份查看城市，点击城市或任意分布条查看用户列表。
      </p>
    </div>
  );
}
