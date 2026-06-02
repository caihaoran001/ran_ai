import Link from "next/link";
import { api } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

type AICostCaseItem = {
  service_case_id: number;
  case_no: string;
  user_id?: number | null;
  user_name?: string | null;
  group_id: string;
  total_calls: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_yuan: number;
  last_call_at?: string | null;
};

export default async function AICostsPage() {
  const items = await api<AICostCaseItem[]>("/dashboard/ai-costs").catch(() => []);
  const totalCost = items.reduce((sum, item) => sum + (item.estimated_cost_yuan || 0), 0);
  const totalTokens = items.reduce((sum, item) => sum + (item.total_tokens || 0), 0);
  const totalCalls = items.reduce((sum, item) => sum + (item.total_calls || 0), 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">AI 调用成本明细</h1>
          <p className="mt-1 text-sm text-zinc-500">
            按服务单聚合 LLM 调用次数、token 和估算金额。金额取决于后台配置的模型单价。
          </p>
        </div>
        <Link className="text-sm font-medium text-blue-600 hover:text-blue-700" href="/dashboard">
          返回数据看板
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <SummaryCard label="总估算成本" value={`¥${totalCost.toFixed(2)}`} />
        <SummaryCard label="总 Tokens" value={totalTokens.toLocaleString("zh-CN")} />
        <SummaryCard label="总调用次数" value={totalCalls.toLocaleString("zh-CN")} />
      </div>

      <section className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-sm">
        <div className="border-b border-zinc-100 px-5 py-4">
          <h2 className="font-semibold text-zinc-900">用户成本列表</h2>
        </div>
        {items.length ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs font-semibold text-zinc-500">
                <tr>
                  <th className="px-5 py-3">用户 / 服务单</th>
                  <th className="px-5 py-3">调用次数</th>
                  <th className="px-5 py-3">Tokens</th>
                  <th className="px-5 py-3">输入 / 输出</th>
                  <th className="px-5 py-3">估算成本</th>
                  <th className="px-5 py-3">最后调用</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {items.map((item) => (
                  <tr key={item.service_case_id} className="hover:bg-zinc-50/70">
                    <td className="px-5 py-4">
                      <div className="font-medium text-zinc-900">{item.user_name || "未命名用户"}</div>
                      <Link className="text-xs text-blue-600 hover:text-blue-700" href={`/cases/${item.service_case_id}`}>
                        {item.case_no}
                      </Link>
                      <div className="mt-1 max-w-[260px] truncate text-xs text-zinc-400">群：{item.group_id}</div>
                    </td>
                    <td className="px-5 py-4 font-medium text-zinc-900">{item.total_calls}</td>
                    <td className="px-5 py-4">{item.total_tokens.toLocaleString("zh-CN")}</td>
                    <td className="px-5 py-4 text-zinc-500">
                      {item.prompt_tokens.toLocaleString("zh-CN")} / {item.completion_tokens.toLocaleString("zh-CN")}
                    </td>
                    <td className="px-5 py-4 font-semibold text-zinc-900">¥{item.estimated_cost_yuan.toFixed(2)}</td>
                    <td className="px-5 py-4 text-zinc-500">{formatChinaDateTime(item.last_call_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-10 text-center text-sm text-zinc-500">暂无 AI 调用记录。</div>
        )}
      </section>
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      <div className="mt-2 text-2xl font-bold text-zinc-900">{value}</div>
    </div>
  );
}
