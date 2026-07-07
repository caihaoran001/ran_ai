import Link from "next/link";
import { api, ServiceCase } from "@/lib/api";
import { DetailHeader } from "../_components/dashboard-detail";

export default async function RefundPendingPage() {
  const cases = await api<ServiceCase[]>("/service-cases?filter=refund_pending").catch(() => []);

  return (
    <div className="page-stack">
      <DetailHeader
        title="已退款待确认"
        subtitle={`AI 识别到 ${cases.length} 个客户可能已完成退款，等待运营确认。确认后将停止打卡、阶段报告等产品类主动推送，群内 AI 聊天仍保留。`}
      />
      <div className="mobile-card-list">
        {cases.map((item) => (
          <Link key={item.id} href={`/cases/${item.id}`} className="mobile-record-card transition-colors hover:border-cyan-300 hover:bg-cyan-50/30">
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-break font-semibold text-slate-950">{item.case_no}</div>
                <div className="mt-1 text-sm text-slate-600">{item.user?.name ?? "-"}</div>
              </div>
              <span className="status-pill border-rose-200 bg-rose-50 text-rose-700">待确认</span>
            </div>
            <div className="text-break mt-3 text-sm text-slate-600">群 ID：{item.group_id}</div>
            <span className="admin-link mt-2 inline-flex">去确认</span>
          </Link>
        ))}
        {cases.length === 0 && (
          <div className="mobile-record-card py-8 text-center text-sm text-slate-500">暂无待确认的退款客户。</div>
        )}
      </div>

      <div className="table-shell hidden md:block">
        <table className="data-table">
          <thead>
            <tr>
              <th className="px-4 py-3">编号</th>
              <th className="px-4 py-3">用户</th>
              <th className="px-4 py-3">群 ID</th>
              <th className="px-4 py-3">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((item) => (
              <tr key={item.id} className="border-t border-zinc-100">
                <td className="px-4 py-3">
                  <Link className="admin-link" href={`/cases/${item.id}`}>
                    {item.case_no}
                  </Link>
                </td>
                <td className="px-4 py-3">{item.user?.name ?? "-"}</td>
                <td className="text-break px-4 py-3">{item.group_id}</td>
                <td className="px-4 py-3">
                  <Link className="admin-link" href={`/cases/${item.id}`}>
                    去确认
                  </Link>
                </td>
              </tr>
            ))}
            {cases.length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-zinc-500" colSpan={4}>
                  暂无待确认的退款客户。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
