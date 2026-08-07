import { api } from "@/lib/api";
import { DetailHeader, CaseInsightList, type DashboardCaseInsightItem } from "../_components/dashboard-detail";

export default async function LostPage() {
  const items = await api<DashboardCaseInsightItem[]>("/dashboard/lost-cases").catch(() => []);

  return (
    <div className="page-stack">
      <DetailHeader
        title="流失用户"
        subtitle={`当前共有 ${items.length} 个服务中用户本人已超过 7 天没有发言（班主任发言不计），下面直接列出用户、服务单和闲置天数。`}
      />
      <CaseInsightList items={items} emptyText="暂无流失用户。" />
    </div>
  );
}
