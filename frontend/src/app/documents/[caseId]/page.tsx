import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { api, UserDocument } from "@/lib/api";
import { DocumentEditor } from "./document-editor";

export default async function DocumentsPage({ 
  params,
}: { 
  params: Promise<{ caseId: string }>;
}) {
  const { caseId: documentId } = await params;

  const document = await api<UserDocument>(`/documents/${documentId}`);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/documents"
          className="mb-3 inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-bold text-slate-700 shadow-xs transition-colors hover:border-cyan-300 hover:bg-cyan-50/60 hover:text-cyan-800"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          返回审核控制台
        </Link>
        <h1 className="text-2xl font-semibold">交付文件审核</h1>
        <p className="mt-1 text-sm text-slate-500">查看内容、编辑修改、下载 docx 或确认发送到群。</p>
      </div>
      <div className="grid gap-4">
        <DocumentEditor document={document} />
      </div>
    </div>
  );
}
