import { api } from "@/lib/api";
import { DetailHeader, MessageEventList, type DashboardCaseMessageEvents } from "../_components/dashboard-detail";

export default async function RefundIntentsPage() {
  const items = await api<DashboardCaseMessageEvents[]>("/dashboard/intent-events/refund").catch(() => []);
  const eventCount = items.reduce((sum, item) => sum + item.events.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="退款/退出意向"
        subtitle={`当前共有 ${items.length} 个服务单、${eventCount} 条退款或退出意向证据。`}
      />
      <MessageEventList items={items} emptyText="暂无退款/退出意向。" />
    </div>
  );
}
