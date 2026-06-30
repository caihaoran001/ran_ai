import { api } from "@/lib/api";
import { CheckinDetailList, DetailHeader, type DashboardCaseCheckins } from "../_components/dashboard-detail";

export default async function TodayCheckinsPage() {
  const items = await api<DashboardCaseCheckins[]>("/dashboard/today-checkins").catch(() => []);
  const checkinCount = items.reduce((sum, item) => sum + item.checkins.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="今日打卡"
        subtitle={`今天共有 ${items.length} 个服务单、${checkinCount} 条打卡记录。`}
      />
      <CheckinDetailList items={items} />
    </div>
  );
}
