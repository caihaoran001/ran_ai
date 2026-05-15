import Link from "next/link";
import { api, UserDocument, ServiceCase } from "@/lib/api";
import { AutoSendToggle } from "./auto-send-toggle";

export default async function DocumentsListPage() {
  const serviceCases = await api<ServiceCase[]>("/service-cases").catch(() => []);

  const allDocuments: Array<UserDocument & { case_no: string; group_id: string; user_name: string; user_phone: string }> = [];
  for (const serviceCase of serviceCases) {
    const documents = await api<UserDocument[]>(`/service-cases/${serviceCase.id}/documents`).catch(() => []);
    for (const doc of documents) {
      allDocuments.push({ 
        ...doc, 
        case_no: serviceCase.case_no, 
        group_id: serviceCase.group_id,
        user_name: serviceCase.user?.name || "未知",
        user_phone: serviceCase.user?.phone || "无手机号"
      });
    }
  }

  const statusConfig: Record<string, { label: string; className: string }> = {
    reviewing: { label: "待审核", className: "bg-yellow-50 text-yellow-700 border-yellow-200" },
    approved: { label: "已批准", className: "bg-green-50 text-green-700 border-green-200" },
    sent: { label: "已发送", className: "bg-blue-50 text-blue-700 border-blue-200" },
    draft: { label: "草稿", className: "bg-zinc-50 text-zinc-600 border-zinc-200" },
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">文档审核</h1>
        <p className="mt-1 text-sm text-zinc-500">审核交付文件、编辑内容、确认后发送到群</p>
      </div>

      <AutoSendToggle initialValue={false} />

      {allDocuments.length > 0 ? (
        <div className="space-y-3">
          {allDocuments.map((doc) => {
            const status = statusConfig[doc.status] || { label: doc.status, className: "bg-zinc-50 text-zinc-600" };
            return (
              <Link
                key={doc.id}
                href={`/documents/${doc.service_case_id}?documentId=${doc.id}`}
                className="flex items-center justify-between rounded-xl border border-zinc-200 bg-white p-5 shadow-sm transition-all hover:border-blue-300 hover:shadow-md"
              >
                <div className="flex items-center gap-4">
                  <div className="text-2xl">📄</div>
                  <div>
                    <div className="font-semibold text-zinc-900">{doc.title}</div>
                    <div className="mt-1 text-sm text-zinc-500">
                      {doc.user_name} · {doc.user_phone} · {doc.case_no}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <span className={`rounded-full border px-3 py-1 text-xs font-medium ${status.className}`}>
                    {status.label}
                  </span>
                  <span className="text-sm text-blue-600">去审核 →</span>
                </div>
              </Link>
            );
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-zinc-200 bg-white p-12 text-center text-zinc-400">
          <div className="mb-3 text-5xl">📄</div>
          <p className="text-lg">暂无交付文件</p>
          <p className="mt-2 text-sm">用户提交问卷后会自动生成文档</p>
        </div>
      )}
    </div>
  );
}
