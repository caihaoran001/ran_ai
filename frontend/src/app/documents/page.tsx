import Link from "next/link";
import {
  api,
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
import { FAQEvolutionCenter, ExcellentCaseEvolutionCenter } from "./knowledge-draft-group";
import { DocumentSearchList } from "./document-search-list";
import { ReviewConsoleLiveRefresh } from "./review-console-live-refresh";
import { Bot, CheckCircle2, MessageCircle } from "lucide-react";

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

  return (
    <div className="page-stack">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="page-heading">审核控制台</h1>
          <p className="page-subtitle">集中管理交付文件与 AI 聊天回复，确保输出安全、准确。</p>
        </div>
        <ReviewConsoleLiveRefresh />
      </div>

      {/* 置顶全局开关 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <AutoSendToggle initialValue={policy.mode === "auto_send"} />
        <AiReplyToggle initialValue={aiReplyPolicy.mode === "auto_send"} />
      </div>

      {/* 1. 交付文件控制台 */}
      <DocumentSearchList />

      {/* 2. AI回复待审核 */}
      <PendingMessageGroup messages={pendingMessages} casesById={casesById} />

      {/* 3. 参考问答库 (FAQ) 进化工作台 */}
      <FAQEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
        indexStats={knowledgeIndexStats}
        faqs={knowledgeFaqs}
        evolutionConfig={knowledgeEvolutionConfig}
      />

      {/* 4. 优秀标杆案例库进化工作台 */}
      <ExcellentCaseEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
        evolutionConfig={knowledgeEvolutionConfig}
      />
    </div>
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
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <div className="flex items-center justify-between">
          <h2 className="panel-title">AI 回复待审核</h2>
          <span className="status-pill border-amber-200 bg-amber-50 text-amber-700">
            {messages.length} 条待处理
          </span>
        </div>
        <p className="panel-subtitle">高风险、人工介入或手动审核模式下生成的机器人回复，确认后才发送到群。</p>
      </div>
      <div className="p-5">
        {messages.length > 0 ? (
          <div className="space-y-5">
            {messages.map((message) => {
              const serviceCase = message.service_case_id ? casesById.get(message.service_case_id) : null;
              return (
                <div key={message.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition-colors hover:border-cyan-200">
                  <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/80 px-5 py-2.5 text-xs text-slate-500">
                    <span>
                      服务单：
                      {serviceCase ? (
                        <Link className="font-bold text-cyan-700 hover:underline" href={`/cases/${serviceCase.id}`}>
                          {serviceCase.case_no}
                        </Link>
                      ) : (
                        <span className="font-medium text-slate-400">未绑定</span>
                      )}
                    </span>
                    <span className="font-medium text-slate-400">{message.created_at}</span>
                  </div>
                  <div className="grid divide-y divide-slate-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
                    <div className="bg-slate-50/60 p-5">
                      <div className="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
                        <MessageCircle className="h-3.5 w-3.5" />
                        客户说
                      </div>
                      <div className="text-sm font-medium leading-relaxed text-slate-700">
                        {message.trigger?.content || <span className="italic text-slate-400">（未能关联到原始消息）</span>}
                      </div>
                    </div>
                    <div className="bg-white p-5">
                      <div className="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
                        <Bot className="h-3.5 w-3.5" />
                        AI 拟回复
                      </div>
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
          <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400">
            <CheckCircle2 className="mx-auto mb-3 h-9 w-9 text-emerald-500" />
            <p className="text-sm font-medium">暂无待审核 AI 回复</p>
          </div>
        )}
      </div>
    </section>
  );
}
