import { api, UserDocument } from "@/lib/api";
import { DocumentEditor } from "./document-editor";

export default async function DocumentsPage({ 
  params, 
  searchParams 
}: { 
  params: Promise<{ caseId: string }>;
  searchParams: Promise<{ documentId?: string }>;
}) {
  const { caseId } = await params;
  const { documentId } = await searchParams;

  let documents: UserDocument[] = [];
  
  if (documentId) {
    const document = await api<UserDocument>(`/documents/${documentId}`).catch(() => null);
    if (document) {
      documents = [document];
    }
  } else {
    documents = await api<UserDocument[]>(`/service-cases/${caseId}/documents`).catch(() => []);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">交付文件审核</h1>
        <p className="mt-1 text-sm text-zinc-500">
          {documentId ? "查看内容、编辑修改、下载 docx 或确认发送到群。" : "查看结构化正文、微调内容、下载 docx，确认后由企微机器人发送文件。"}
        </p>
      </div>
      <div className="grid gap-4">
        {documents.map((document) => <DocumentEditor key={document.id} document={document} />)}
        {documents.length === 0 && <div className="rounded-lg border border-zinc-200 bg-white p-6 text-zinc-500">暂无交付文件</div>}
      </div>
    </div>
  );
}
