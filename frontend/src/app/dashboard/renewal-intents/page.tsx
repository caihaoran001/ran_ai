import { api } from "@/lib/api";
import { DetailHeader, MessageEventList, type DashboardCaseMessageEvents } from "../_components/dashboard-detail";

export default async function RenewalIntentsPage() {
  const items = await api<DashboardCaseMessageEvents[]>("/dashboard/intent-events/renewal").catch(() => []);
  const eventCount = items.reduce((sum, item) => sum + item.events.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="明确续购意向"
        subtitle={`当前共有 ${items.length} 个服务单、${eventCount} 条续购/复购意向证据。`}
      />
      <MessageEventList items={items} emptyText="暂无明确续购意向。" />
    </div>
  );
}
