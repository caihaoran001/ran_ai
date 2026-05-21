import Link from "next/link";
import {
  api,
  UserDocument,
  ServiceCase,
  Message,
  KnowledgeDraft,
  KnowledgeIndexStats,
  KnowledgeFAQ,
  KnowledgeEvolutionSetting,
} from "@/lib/api";
import { AutoSendToggle } from "./auto-send-toggle";
import { AiReplyToggle } from "./ai-reply-toggle";
import { PendingMessageActions } from "./pending-message-actions";
import { RiskReplyConfig } from "./risk-reply-config";
import {
  KnowledgeDraftActions,
  KnowledgeDraftScanButton,
  KnowledgeEvolutionConfigPanel,
  KnowledgeFAQEditor,
  KnowledgeIndexSyncButton,
} from "./knowledge-draft-actions";

export default async function DocumentsListPage() {
  const serviceCases = await api<ServiceCase[]>("/service-cases").catch(() => []);
  const policy = await api<{ mode: string }>("/settings/content-delivery-policy").catch(() => ({ mode: "manual_review" }));
  const aiReplyPolicy = await api<{ mode: string }>("/settings/ai-reply-delivery-policy").catch(() => ({ mode: "manual_review" }));
  const pendingMessages = await api<Message[]>("/messages/pending-ai").catch(() => []);
  const pendingKnowledgeDrafts = await api<KnowledgeDraft[]>("/knowledge-drafts?status=pending").catch(() => []);
  const approvedKnowledgeDrafts = await api<KnowledgeDraft[]>("/knowledge-drafts?status=approved").catch(() => []);
  const knowledgeFaqs = await api<KnowledgeFAQ[]>("/knowledge-faqs?status=all").catch(() => []);
  const knowledgeEvolutionConfig = await api<KnowledgeEvolutionSetting>("/settings/knowledge-evolution").catch(() => ({
    enabled: false,
    interval_seconds: 3600,
    qa_at: "00:00",
    daily_qa_limit: 500,
    case_days: "3,7,14,21,28",
    case_limit_per_case: 200,
    timezone: "Asia/Shanghai",
  }));
  const knowledgeIndexStats = await api<KnowledgeIndexStats>("/knowledge-base/index/stats").catch(() => ({
    total_chunks: 0,
    embedded_chunks: 0,
    by_source_type: {},
  }));
  const casesById = new Map(serviceCases.map((item) => [item.id, item]));

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
  const pendingDocuments = allDocuments.filter((doc) => doc.status === "reviewing" || doc.status === "draft");
  const reviewedDocuments = allDocuments.filter((doc) => doc.status !== "reviewing" && doc.status !== "draft");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">审核控制台</h1>
        <p className="mt-1 text-sm text-zinc-500">集中管理交付文件与 AI 聊天回复，确保输出安全、准确。</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <AutoSendToggle initialValue={policy.mode === "auto_send"} />
        <AiReplyToggle initialValue={aiReplyPolicy.mode === "auto_send"} />
        <RiskReplyConfig />
      </div>

      <PendingMessageGroup messages={pendingMessages} casesById={casesById} />

      <KnowledgeDraftGroup
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
        indexStats={knowledgeIndexStats}
        faqs={knowledgeFaqs}
        evolutionConfig={knowledgeEvolutionConfig}
      />

      {allDocuments.length > 0 ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <DocumentGroup
            title="文件待审核"
            description="新生成、尚未确认发送的交付文件"
            documents={pendingDocuments}
            statusConfig={statusConfig}
            emptyText="暂无待审核文件"
          />
          <DocumentGroup
            title="已审核"
            description="已批准或已发送的交付文件"
            documents={reviewedDocuments}
            statusConfig={statusConfig}
            emptyText="暂无已审核文件"
          />
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

function KnowledgeDraftGroup({
  pendingDrafts,
  approvedDrafts,
  indexStats,
  faqs,
  evolutionConfig,
}: {
  pendingDrafts: KnowledgeDraft[];
  approvedDrafts: KnowledgeDraft[];
  indexStats: KnowledgeIndexStats;
  faqs: KnowledgeFAQ[];
  evolutionConfig: KnowledgeEvolutionSetting;
}) {
  const sourceText = Object.entries(indexStats.by_source_type)
    .map(([source, count]) => `${source} ${count}`)
    .join(" · ");

  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="border-b border-zinc-100 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-zinc-900">知识草稿</h2>
            <p className="mt-1 text-sm text-zinc-500">
              FAQ 按自然日聊天沉淀；优秀案例按 3/7/14/21/28 天阶段窗口沉淀，批准后才进入知识库或案例库。
            </p>
          </div>
          <KnowledgeDraftScanButton />
        </div>
      </div>
      <div className="border-b border-zinc-100 bg-zinc-50/50 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <div className="text-xs text-zinc-500">数据库知识块</div>
              <div className="mt-1 text-xl font-semibold text-zinc-900">{indexStats.total_chunks}</div>
            </div>
            <div>
              <div className="text-xs text-zinc-500">已向量化</div>
              <div className="mt-1 text-xl font-semibold text-zinc-900">{indexStats.embedded_chunks}</div>
            </div>
            <div>
              <div className="text-xs text-zinc-500">来源</div>
              <div className="mt-1 max-w-md truncate text-sm text-zinc-700">{sourceText || "暂无"}</div>
            </div>
          </div>
          <KnowledgeIndexSyncButton />
        </div>
      </div>
      <div className="border-b border-zinc-100 p-4">
        <KnowledgeEvolutionConfigPanel initialConfig={evolutionConfig} />
      </div>
      <div className="grid gap-4 p-4 xl:grid-cols-[1.5fr_1fr_1.2fr]">
        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-700">待审核</h3>
            <span className="text-sm text-zinc-500">{pendingDrafts.length} 条</span>
          </div>
          {pendingDrafts.length > 0 ? (
            <div className="space-y-4">
              {pendingDrafts.map((draft) => (
                <div key={draft.id} className="rounded-lg border border-zinc-200 p-4">
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                    <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-700">
                      {draft.draft_type === "case" ? "优秀案例" : "FAQ"}
                    </span>
                    {draft.category && <span>{draft.category}</span>}
                    <span>{draft.created_at}</span>
                  </div>
                  {draft.reason && <p className="mb-3 text-sm text-zinc-500">入选原因：{draft.reason}</p>}
                  {draft.evidence && <p className="mb-3 rounded-md bg-zinc-50 p-3 text-sm text-zinc-600">证据：{draft.evidence}</p>}
                  <KnowledgeDraftActions
                    draftId={draft.id}
                    initialTitle={draft.title}
                    initialQuestion={draft.question}
                    initialAnswer={draft.answer}
                    initialCategory={draft.category}
                    initialTags={draft.tags}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-zinc-200 py-10 text-center text-sm text-zinc-400">
              暂无待审核知识草稿
            </div>
          )}
        </div>
        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-700">已入库</h3>
            <span className="text-sm text-zinc-500">{approvedDrafts.length} 条</span>
          </div>
          <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
            {approvedDrafts.slice(0, 20).map((draft) => (
              <div key={draft.id} className="rounded-lg border border-zinc-200 p-3 text-sm">
                <div className="font-medium text-zinc-800">{draft.title}</div>
                <div className="mt-1 text-xs text-zinc-500">
                  {draft.draft_type === "case" ? "优秀案例" : "FAQ"} · {draft.published_to || "已入库"}
                </div>
              </div>
            ))}
            {approvedDrafts.length === 0 && (
              <div className="rounded-lg border border-dashed border-zinc-200 py-10 text-center text-sm text-zinc-400">
                暂无已入库草稿
              </div>
            )}
          </div>
        </div>
        <KnowledgeFAQEditor faqs={faqs} />
      </div>
    </section>
  );
}

function PendingMessageGroup({
  messages,
  casesById,
}: {
  messages: Message[];
  casesById: Map<number, ServiceCase>;
}) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="border-b border-zinc-100 px-5 py-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-zinc-900">AI 回复待审核</h2>
          <span className="text-sm text-zinc-500">{messages.length} 条</span>
        </div>
        <p className="mt-1 text-sm text-zinc-500">高风险、人工介入或手动审核模式下生成的机器人回复，确认后才发送到群。</p>
      </div>
      <div className="p-4">
        {messages.length > 0 ? (
          <div className="space-y-4">
            {messages.map((message) => {
              const serviceCase = message.service_case_id ? casesById.get(message.service_case_id) : null;
              return (
                <div key={message.id} className="overflow-hidden rounded-lg border border-zinc-200">
                  <div className="border-b border-zinc-100 bg-zinc-50/50 px-4 py-2 text-xs text-zinc-500">
                    {serviceCase ? (
                      <Link className="font-medium text-blue-600 hover:underline" href={`/cases/${serviceCase.id}`}>
                        {serviceCase.case_no}
                      </Link>
                    ) : (
                      "未绑定服务单"
                    )}
                    <span> · {message.created_at}</span>
                  </div>
                  <div className="grid divide-x divide-zinc-100 lg:grid-cols-2">
                    <div className="bg-zinc-50/30 p-4">
                      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">客户说：</div>
                      <div className="text-sm leading-relaxed text-zinc-700">
                        {message.trigger?.content || <span className="italic text-zinc-400">（未能关联到原始消息）</span>}
                      </div>
                    </div>
                    <div className="p-4">
                      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">AI 拟回复：</div>
                      <PendingMessageActions
                        messageId={message.id}
                        serviceCaseId={message.service_case_id}
                        initialContent={message.content}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="py-10 text-center text-sm text-zinc-400">暂无待审核 AI 回复</div>
        )}
      </div>
    </section>
  );
}

function DocumentGroup({
  title,
  description,
  documents,
  statusConfig,
  emptyText,
}: {
  title: string;
  description: string;
  documents: Array<UserDocument & { case_no: string; group_id: string; user_name: string; user_phone: string }>;
  statusConfig: Record<string, { label: string; className: string }>;
  emptyText: string;
}) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="border-b border-zinc-100 px-5 py-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-zinc-900">{title}</h2>
          <span className="text-sm text-zinc-500">{documents.length} 份</span>
        </div>
        <p className="mt-1 text-sm text-zinc-500">{description}</p>
      </div>
      <div className="p-4">
        {documents.length > 0 ? (
          <div className="max-h-[400px] overflow-y-auto space-y-3 pr-1">
            {documents.map((doc) => {
              const status = statusConfig[doc.status] || { label: doc.status, className: "bg-zinc-50 text-zinc-600" };
              return (
                <Link
                  key={doc.id}
                  href={`/documents/${doc.id}`}
                  className="block rounded-lg border border-zinc-200 p-4 transition-all hover:border-blue-300 hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="font-semibold text-zinc-900">{doc.title}</div>
                      <div className="mt-1 text-sm text-zinc-500">
                        {doc.user_name} · {doc.user_phone} · {doc.case_no}
                      </div>
                    </div>
                    <span className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${status.className}`}>
                      {status.label}
                    </span>
                  </div>
                  <div className="mt-3 text-sm text-blue-600">查看详情 →</div>
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="py-10 text-center text-sm text-zinc-400">{emptyText}</div>
        )}
      </div>
    </section>
  );
}
