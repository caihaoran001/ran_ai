import { TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import {
  AgentOperationsClient,
  AgentOperationsData,
} from "@/components/agent-operations/agent-operations-client";

const TABS = ["overview", "reply", "archivist", "costs"] as const;

export default async function AgentOperationsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; days?: string }>;
}) {
  const params = await searchParams;
  const tab = TABS.includes(params.tab as any)
    ? (params.tab as (typeof TABS)[number])
    : "overview";
  const parsedDays = Number(params.days);
  const days = [7, 30, 90].includes(parsedDays) ? parsedDays : 30;
  
  const data = await api<AgentOperationsData>(
    `/agent-operations?days=${days}`,
  ).catch(() => null);

  if (!data) {
    return (
      <div className="page-stack">
        <div className="empty-state py-20">
          <TriangleAlert className="h-8 w-8 text-amber-500 mb-2" />
          <p className="text-base font-bold text-slate-800">
            AI 运行统计数据暂时无法读取
          </p>
          <p className="text-xs text-slate-500 mt-1 max-w-md">
            可能是后端服务正在升级或连接超时，请尝试刷新页面。
          </p>
        </div>
      </div>
    );
  }

  return (
    <AgentOperationsClient
      data={data}
      initialTab={tab}
      initialDays={days}
    />
  );
}
