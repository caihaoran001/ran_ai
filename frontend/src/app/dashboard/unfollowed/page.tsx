import { api } from "@/lib/api";
import { DetailHeader, CaseInsightList, type DashboardCaseInsightItem } from "../_components/dashboard-detail";

export default async function UnfollowedPage() {
  const items = await api<DashboardCaseInsightItem[]>("/dashboard/unfollowed-cases").catch(() => []);

  return (
    <div className="page-stack">
      <DetailHeader
        title="未跟进（2-7天）"
        subtitle={`当前共有 ${items.length} 个服务中用户已 2-7 天没有入站消息，下面直接列出用户、服务单和闲置天数。`}
      />
      <CaseInsightList items={items} emptyText="暂无未跟进用户。" />
    </div>
  );
}
