import { api, UserDocument } from "@/lib/api";
import { BackIconLink } from "@/components/back-icon-link";
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
      <div className="flex items-start gap-3">
        <BackIconLink href="/documents" label="返回审核控制台" />
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">交付文件审核</h1>
          <p className="mt-1 text-sm text-slate-500">查看内容、编辑修改、下载 docx 或确认发送到群。</p>
        </div>
      </div>
      <div className="grid gap-4">
        <DocumentEditor document={document} />
      </div>
    </div>
  );
}
