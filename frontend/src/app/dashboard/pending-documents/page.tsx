import { api } from "@/lib/api";
import { DetailHeader, PendingDocumentList, type DashboardCasePendingDocuments } from "../_components/dashboard-detail";

export default async function PendingDocumentsPage() {
  const items = await api<DashboardCasePendingDocuments[]>("/dashboard/pending-documents").catch(() => []);
  const documentCount = items.reduce((sum, item) => sum + item.documents.length, 0);

  return (
    <div className="page-stack">
      <DetailHeader
        title="待审核文件"
        subtitle={`当前共有 ${items.length} 个服务单、${documentCount} 份最新交付文件处于草稿或待审核状态。`}
      />
      <PendingDocumentList items={items} />
    </div>
  );
}
