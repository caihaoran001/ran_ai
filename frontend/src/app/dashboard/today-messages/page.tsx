import { api } from "@/lib/api";
import { DetailHeader, MessageEventList, type DashboardCaseMessageEvents } from "../_components/dashboard-detail";

export default async function TodayMessagesPage() {
  const items = await api<DashboardCaseMessageEvents[]>("/dashboard/today-messages").catch(() => []);
  const eventCount = items.reduce((sum, item) => sum + item.events.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="今日用户消息"
        subtitle={`今天共有 ${items.length} 个服务单、${eventCount} 条用户入站消息。`}
      />
      <MessageEventList items={items} emptyText="今天暂无用户消息。" />
    </div>
  );
}
