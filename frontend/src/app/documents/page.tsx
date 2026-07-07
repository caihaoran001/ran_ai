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
import { RefundReviewActions } from "./refund-review-actions";
import { Bot, CheckCircle2, Clock, MessageCircle, RefreshCcw } from "lucide-react";

export default async function DocumentsListPage() {
  const [
    policy,
    aiReplyPolicy,
    pendingMessages,
    refundPendingCases,
    pendingKnowledgeDrafts,
    approvedKnowledgeDrafts,
    knowledgeFaqs,
    knowledgeEvolutionConfig,
    knowledgeIndexStats,
  ] = await Promise.all([
    api<{ mode: string }>("/settings/content-delivery-policy").catch(() => ({ mode: "manual_review" })),
    api<{ mode: string }>("/settings/ai-reply-delivery-policy").catch(() => ({ mode: "manual_review" })),
    api<Message[]>("/messages/pending-ai").catch(() => []),
    api<ServiceCase[]>("/service-cases?filter=refund_pending").catch(() => []),
    api<KnowledgeDraft[]>("/knowledge-drafts?status=pending").catch(() => []),
    api<KnowledgeDraft[]>("/knowledge-drafts?status=approved").catch(() => []),
    api<KnowledgeFAQ[]>("/knowledge-faqs?status=all").catch(() => []),
    api<KnowledgeEvolutionSetting>("/settings/knowledge-evolution").catch(() => ({
      enabled: false,
      interval_seconds: 3600,
      qa_at: "00:00",
      daily_qa_limit: 500,
      case_days: "3,7,14,21,28",
      case_limit_per_case: 200,
      timezone: "Asia/Shanghai",
    })),
    api<KnowledgeIndexStats>("/knowledge-base/index/stats").catch(() => ({
      total_chunks: 0,
      embedded_chunks: 0,
      by_source_type: {},
    })),
  ]);
  const serviceCases = pendingMessages.length > 0
    ? await api<ServiceCase[]>("/service-cases").catch(() => [])
    : [];
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

      {/* 2. 退款审核 */}
      <RefundReviewGroup cases={refundPendingCases} />

      {/* 2b. AI回复待审核（仅当人工审核模式下积压了待审核回复时才显示） */}
      {pendingMessages.length > 0 && (
        <PendingMessageGroup messages={pendingMessages} casesById={casesById} />
      )}

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

type RiskItem = { code?: string; level?: string; reason?: string; evidence?: string };

// 从触发消息的 analysis_json 提取「转人工原因」与事件风险等级
function extractTriggerInsight(aj?: Record<string, unknown> | null): { reasons: string[]; level: string | null } {
  if (!aj) return { reasons: [], level: null };
  const arr = (k: string) => (Array.isArray(aj[k]) ? (aj[k] as RiskItem[]) : []);
  const items = [...arr("intervention_reasons"), ...arr("risk_flags")];
  const reasons = Array.from(
    new Set(items.map((i) => i?.reason).filter((r): r is string => Boolean(r && r.trim()))),
  );
  const levels = items.map((i) => i?.level);
  const level = levels.includes("high")
    ? "high"
    : levels.includes("medium")
      ? "medium"
      : levels.includes("low")
        ? "low"
        : null;
  return { reasons, level };
}

const RISK_BADGE: Record<string, string> = {
  high: "border-rose-200 bg-rose-50 text-rose-700",
  medium: "border-amber-200 bg-amber-50 text-amber-700",
  low: "border-yellow-200 bg-yellow-50 text-yellow-700",
};
const RISK_LABEL: Record<string, string> = { high: "高风险", medium: "中风险", low: "低风险" };

// 把后端 UTC 时间(ISO，带 T/Z)格式化成北京时间自然显示
function formatBeijing(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

// 取最新一条退款意向标记的证据原话（refund_intent_flags 末尾一条）
function latestRefundEvidence(flags?: Array<Record<string, string | boolean>> | null): string {
  if (!flags?.length) return "";
  const latest = flags[flags.length - 1];
  const evidence = typeof latest.evidence === "string" ? latest.evidence : "";
  const reason = typeof latest.reason === "string" ? latest.reason : "";
  return evidence || reason || "";
}

function RefundReviewGroup({ cases }: { cases: ServiceCase[] }) {
  return (
    <section id="refund-reviews" className="panel overflow-hidden scroll-mt-6">
      <div className="panel-header">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="panel-title">退款审核</h2>
          <span className="status-pill border-rose-200 bg-rose-50 text-rose-700">
            {cases.length} 单待确认
          </span>
        </div>
        <p className="panel-subtitle">AI 从群聊识别到客户已发起/申请退款，等待运营核实。确认后将停止打卡提醒、阶段报告等产品类主动推送，群内 AI 聊天仍保留。</p>
      </div>
      <div className="p-5">
        {cases.length > 0 ? (
          <div className="space-y-4">
            {cases.map((item) => {
              const evidence = latestRefundEvidence(item.refund_intent_flags);
              return (
                <div key={item.id} className="flex flex-col gap-3 rounded-xl border border-rose-100 bg-rose-50/40 px-4 py-4 shadow-sm transition-colors hover:border-rose-200 sm:flex-row sm:items-start sm:justify-between sm:px-5">
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                      <span className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-100 px-2 py-0.5 font-semibold text-rose-700">
                        <RefreshCcw className="h-3.5 w-3.5" />
                        疑似已退款
                      </span>
                      <Link className="text-break font-bold text-cyan-700 hover:underline" href={`/cases/${item.id}`}>
                        {item.case_no}
                      </Link>
                      {item.user?.name && <span className="font-semibold text-slate-600">· {item.user.name}</span>}
                      {item.group_id && <span className="text-slate-400">· {item.group_id}</span>}
                    </div>
                    {evidence && (
                      <p className="text-break text-sm leading-relaxed text-slate-700">
                        <span className="font-bold text-slate-400">客户说：</span>
                        {evidence}
                      </p>
                    )}
                  </div>
                  <RefundReviewActions caseId={item.id} />
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400">
            <CheckCircle2 className="mx-auto mb-3 h-9 w-9 text-emerald-500" />
            <p className="text-sm font-medium">暂无待审核退款</p>
          </div>
        )}
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
    <section id="pending-reviews" className="panel overflow-hidden scroll-mt-6">
      <div className="panel-header">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="panel-title">需关注事件 · AI 回复待审核</h2>
          <span className="status-pill border-amber-200 bg-amber-50 text-amber-700">
            {messages.length} 条待处理
          </span>
        </div>
        <p className="panel-subtitle">触发转人工、@班主任后待复核的 AI 回复。逐条确认是谁、因哪句话触发，再决定发送或不发送。</p>
      </div>
      <div className="p-5">
        {messages.length > 0 ? (
          <div className="space-y-5">
            {messages.map((message) => {
              const serviceCase = message.service_case_id ? casesById.get(message.service_case_id) : null;
              const { reasons, level } = extractTriggerInsight(message.trigger?.analysis_json);
              const triggerAt = message.trigger?.display_at ?? message.trigger?.created_at ?? message.created_at;
              return (
                <div key={message.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition-colors hover:border-cyan-200">
                  <div className="flex flex-col gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
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
                      {serviceCase?.user?.name && (
                        <span className="font-semibold text-slate-600">· {serviceCase.user.name}</span>
                      )}
                      {level && (
                        <span className={`status-pill ${RISK_BADGE[level]}`}>{RISK_LABEL[level]}</span>
                      )}
                    </div>
                    <span className="inline-flex items-center gap-1 rounded-md bg-cyan-50 px-2 py-0.5 font-medium text-cyan-700">
                      <Clock className="h-3.5 w-3.5" />
                      {formatBeijing(triggerAt)}
                    </span>
                  </div>
                  {reasons.length > 0 && (
                    <div className="text-break border-b border-amber-100 bg-amber-50/70 px-4 py-2 text-xs leading-5 text-amber-800 sm:px-5">
                      <span className="font-bold">转人工原因：</span>
                      {reasons.join("；")}
                    </div>
                  )}
                  <div className="grid divide-y divide-slate-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
                    <div className="bg-slate-50/60 p-5">
                      <div className="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
                        <MessageCircle className="h-3.5 w-3.5" />
                        客户说
                      </div>
                      <div className="text-break text-sm font-medium leading-relaxed text-slate-700">
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
