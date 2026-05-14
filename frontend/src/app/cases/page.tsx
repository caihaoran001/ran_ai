import Link from "next/link";
import { api, ServiceCase } from "@/lib/api";
import { NewCaseForm } from "./new-case-form";

export default async function CasesPage() {
  const cases = await api<ServiceCase[]>("/service-cases").catch(() => []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">服务单</h1>
        <p className="mt-1 text-sm text-zinc-500">一个企业微信群对应一个 28 天服务空间。</p>
      </div>
      <NewCaseForm />
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
                <td className="px-4 py-3">{item.status}</td>
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
