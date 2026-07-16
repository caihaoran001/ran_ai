import { api } from "@/lib/api";
import { DetailHeader, MessageEventList, type DashboardCaseMessageEvents } from "../_components/dashboard-detail";

export default async function AttentionEventsPage() {
  const items = await api<DashboardCaseMessageEvents[]>("/dashboard/attention-events").catch(() => []);
  const eventCount = items.reduce((sum, item) => sum + item.events.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="转人工事件"
        subtitle={`今日共有 ${items.length} 个服务单、${eventCount} 条触发转人工并 @班主任 的事件。`}
      />
      <MessageEventList items={items} emptyText="今日暂无转人工事件。" showAiReply />
    </div>
  );
}
