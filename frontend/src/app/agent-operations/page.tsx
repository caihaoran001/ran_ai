import { TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import {
  AgentOperationsClient,
  AgentOperationsData,
  DeepseekBalance,
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
  // 默认看近 24h 的当前运行状态；历史回溯由运营主动切换。
  const days = [1, 3, 7, 30, 90].includes(parsedDays) ? parsedDays : 1;

  const data = await api<AgentOperationsData>(
    `/agent-operations?days=${days}`,
  ).catch(() => null);
  // 账户余额是外部实时调用，单独取、失败兜底为 null，不拖垮整个 AI 运行页。
  const balance = await api<DeepseekBalance>("/dashboard/deepseek-balance").catch(
    () => null,
  );

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
      balance={balance}
    />
  );
}
