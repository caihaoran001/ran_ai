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
  // 默认只看近 7 天：单次供应商故障等历史尖峰不会永久污染首屏失败率，
  // 需要时可切到 1 天看当前健康、或 30/90 天回溯。
  const days = [1, 7, 30, 90].includes(parsedDays) ? parsedDays : 7;
  
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
