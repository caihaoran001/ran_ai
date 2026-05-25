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
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">审核控制台</h1>
        <p className="mt-1 text-sm text-zinc-500">集中管理交付文件与 AI 聊天回复，确保输出安全、准确。</p>
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
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm overflow-hidden">
      <div className="border-b border-zinc-100 px-6 py-4">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-zinc-900">AI 回复待审核</h2>
          <span className="text-xs font-semibold bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full border border-amber-100/60">
            {messages.length} 条待处理
          </span>
        </div>
        <p className="mt-1 text-sm text-zinc-500">高风险、人工介入或手动审核模式下生成的机器人回复，确认后才发送到群。</p>
      </div>
      <div className="p-5">
        {messages.length > 0 ? (
          <div className="space-y-5">
            {messages.map((message) => {
              const serviceCase = message.service_case_id ? casesById.get(message.service_case_id) : null;
              return (
                <div key={message.id} className="overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/20 shadow-2xs hover:border-zinc-300 transition-all">
                  <div className="border-b border-zinc-100 bg-zinc-50/60 px-5 py-2.5 text-xs text-zinc-500 flex justify-between items-center">
                    <span>
                      服务单：
                      {serviceCase ? (
                        <Link className="font-bold text-blue-600 hover:underline" href={`/cases/${serviceCase.id}`}>
                          {serviceCase.case_no}
                        </Link>
                      ) : (
                        <span className="font-medium text-zinc-400">未绑定</span>
                      )}
                    </span>
                    <span className="text-zinc-400 font-medium">{message.created_at}</span>
                  </div>
                  <div className="grid divide-y divide-zinc-250 lg:grid-cols-2 lg:divide-y-0 lg:divide-x">
                    <div className="bg-zinc-50/40 p-5">
                      <div className="mb-2 text-2xs font-bold uppercase tracking-wider text-zinc-400">💬 客户说：</div>
                      <div className="text-sm leading-relaxed text-zinc-700 font-medium">
                        {message.trigger?.content || <span className="italic text-zinc-400">（未能关联到原始消息）</span>}
                      </div>
                    </div>
                    <div className="p-5 bg-white">
                      <div className="mb-2 text-2xs font-bold uppercase tracking-wider text-zinc-400">🤖 AI 拟回复：</div>
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
          <div className="py-16 text-center text-zinc-400 border border-dashed border-zinc-200 rounded-xl">
            <span className="text-3xl block mb-2">🎉</span>
            <p className="text-sm font-medium">暂无待审核 AI 回复</p>
          </div>
        )}
      </div>
    </section>
  );
}
