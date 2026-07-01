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
      <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-50 text-zinc-500">
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
                  <Link className="font-medium text-cyan-700 hover:underline" href={`/cases/${item.id}`}>
                    {item.case_no}
                  </Link>
                </td>
                <td className="px-4 py-3">{item.user?.name ?? "-"}</td>
                <td className="px-4 py-3">{item.group_id}</td>
                <td className="px-4 py-3">
                  <Link className="text-cyan-700 hover:underline" href={`/cases/${item.id}`}>
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
