import Link from "next/link";
import { RefreshCcw } from "lucide-react";
import { api, ServiceCase } from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  questionnaire_submitted: "已提交问卷",
  generating: "文件生成中",
  documents_generated: "已生成文档",
  reviewing: "审核中",
  approved: "已批准",
  in_service: "服务中",
  completed: "已毕业",
  refunded: "已退款",
};

function CaseStatusCell({ status }: { status: string }) {
  if (status === "refunded") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-300 ring-offset-1">
        <RefreshCcw className="h-3.5 w-3.5" />
        已退款
      </span>
    );
  }
  return <span className="text-zinc-700">{STATUS_LABELS[status] ?? status}</span>;
}

export default async function CasesPage() {
  const cases = await api<ServiceCase[]>("/service-cases").catch(() => []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">服务单</h1>
        <p className="mt-1 text-sm text-zinc-500">一个社群对应一个 28 天服务空间。新服务单由群内入营问卷指令自动创建。</p>
      </div>
      <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-50 text-zinc-500">
            <tr>
              <th className="px-4 py-3">编号</th>
              <th className="px-4 py-3">用户</th>
              <th className="px-4 py-3">群 ID</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3">问卷</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((item) => (
              <tr key={item.id} className="border-t border-zinc-100">
                <td className="px-4 py-3">
                  <Link className="font-medium text-zinc-900 hover:underline" href={`/cases/${item.id}`}>
                    {item.case_no}
                  </Link>
                </td>
                <td className="px-4 py-3">{item.user?.name ?? "-"}</td>
                <td className="px-4 py-3">{item.group_id}</td>
                <td className="px-4 py-3"><CaseStatusCell status={item.status} /></td>
                <td className="px-4 py-3">
                  <Link className="text-zinc-700 hover:underline" href={`/q/${item.questionnaire_token}`}>
                    打开问卷
                  </Link>
                </td>
              </tr>
            ))}
            {cases.length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-zinc-500" colSpan={5}>暂无服务单</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
