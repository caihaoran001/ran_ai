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
  completed: "阶段已完结",
  refunded: "已退款",
};

function CaseStatusCell({ serviceCase }: { serviceCase: ServiceCase }) {
  if (serviceCase.status === "refunded") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-300 ring-offset-1">
        <RefreshCcw className="h-3.5 w-3.5" />
        已退款
      </span>
    );
  }
  if (serviceCase.status === "in_service" && serviceCase.service_continued_at) {
    return <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">续服中</span>;
  }
  if (!serviceCase.ai_introduced_at) {
    return <span className="status-pill pill-warn">待员工引荐</span>;
  }
  return <span className="status-pill border-slate-200 bg-slate-50 text-slate-700">{STATUS_LABELS[serviceCase.status] ?? serviceCase.status}</span>;
}

export default async function CasesPage() {
  const cases = await api<ServiceCase[]>("/service-cases").catch(() => []);

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">服务单</h1>
            <p className="page-subtitle">群内首条消息自动建立服务空间；员工首次 @ 机器人完成引荐后，燃人AI开始参与群聊。</p>
          </div>
          <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">
            {cases.length} 个服务空间
          </span>
        </div>
      </div>

      <div className="mobile-card-list">
        {cases.map((item) => (
          <Link
            key={item.id}
            href={`/cases/${item.id}`}
            prefetch={false}
            className="mobile-record-card transition-colors hover:border-cyan-300 hover:bg-white"
          >
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-break font-semibold text-slate-950">{item.case_no}</div>
                <div className="mt-1 text-sm text-slate-600">{item.user?.name ?? "待识别用户"}</div>
              </div>
              <CaseStatusCell serviceCase={item} />
            </div>
            <div className="mt-3 space-y-1 text-sm text-slate-600">
              <div className="text-break">群 ID：{item.group_id}</div>
              <span className="admin-link inline-flex">打开服务单</span>
            </div>
          </Link>
        ))}
        {cases.length === 0 && (
          <div className="empty-state">暂无服务单</div>
        )}
      </div>

      <div className="table-shell hidden md:block">
        <table className="data-table">
          <thead>
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
              <tr key={item.id}>
                <td>
                  <Link className="admin-link text-slate-950" href={`/cases/${item.id}`} prefetch={false}>
                    {item.case_no}
                  </Link>
                </td>
                <td>{item.user?.name ?? "-"}</td>
                <td className="text-break font-mono text-xs text-slate-500">{item.group_id}</td>
                <td><CaseStatusCell serviceCase={item} /></td>
                <td>
                  <Link className="admin-link" href={`/q/${item.questionnaire_token}`} prefetch={false}>
                    打开问卷
                  </Link>
                </td>
              </tr>
            ))}
            {cases.length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-slate-500" colSpan={5}>暂无服务单</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
