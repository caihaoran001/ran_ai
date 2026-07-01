import { api } from "@/lib/api";
import { DetailHeader, CaseInsightList, type DashboardCaseInsightItem } from "../_components/dashboard-detail";

export default async function RiskUsersPage() {
  const items = await api<DashboardCaseInsightItem[]>("/dashboard/risk-cases").catch(() => []);

  return (
    <div className="page-stack">
      <DetailHeader
        title="风险用户"
        subtitle={`当前共有 ${items.length} 个高风险服务单，下面直接列出用户、服务单和具体风险原因。`}
      />
      <CaseInsightList items={items} emptyText="暂无高风险用户。" />
    </div>
  );
}
