import { api } from "@/lib/api";
import { DetailHeader, CaseInsightList, type DashboardCaseInsightItem } from "../_components/dashboard-detail";

export default async function RenewalFollowupPage() {
  const items = await api<DashboardCaseInsightItem[]>("/dashboard/renewal-followup-cases").catch(() => []);

  return (
    <div className="page-stack">
      <DetailHeader
        title="续购跟进窗口"
        subtitle={`当前共有 ${items.length} 个服务单已服务 18-28 天，适合人工做续购沟通，下面直接列出用户、服务单和服务天数。`}
      />
      <CaseInsightList items={items} emptyText="暂无处于续购跟进窗口的用户。" />
    </div>
  );
}
