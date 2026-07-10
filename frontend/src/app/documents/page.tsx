import Link from "next/link";
import {
  api,
  ServiceCase,
  KnowledgeDraft,
  KnowledgeIndexStats,
  KnowledgeFAQ,
  KnowledgeEvolutionSetting,
  DashboardCasePendingDocuments,
} from "@/lib/api";
import { AutoSendToggle } from "./auto-send-toggle";
import { AiReplyToggle } from "./ai-reply-toggle";
import { AgentRuleEvolutionCenter, FAQEvolutionCenter, ExcellentCaseEvolutionCenter } from "./knowledge-draft-group";
import { DocumentSearchList } from "./document-search-list";
import { ReviewConsoleLiveRefresh } from "./review-console-live-refresh";
import { RefundReviewActions } from "./refund-review-actions";
import { CheckCircle2, RefreshCcw } from "lucide-react";

export default async function DocumentsListPage() {
  const [
    policy,
    aiReplyEnabled,
    refundPendingCases,
    pendingDocumentGroups,
    pendingKnowledgeDrafts,
    approvedKnowledgeDrafts,
    knowledgeFaqs,
    knowledgeEvolutionConfig,
    knowledgeIndexStats,
  ] = await Promise.all([
    api<{ mode: string }>("/settings/content-delivery-policy").catch(() => ({ mode: "manual_review" })),
    api<{ enabled: boolean; auto_disabled_reason: string | null; auto_disabled_at: string | null }>(
      "/settings/ai-reply-enabled",
    ).catch(() => ({ enabled: true, auto_disabled_reason: null, auto_disabled_at: null })),
    api<ServiceCase[]>("/service-cases?filter=refund_pending").catch(() => []),
    api<DashboardCasePendingDocuments[]>("/dashboard/pending-documents").catch(() => []),
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

  const pendingDocumentCount = pendingDocumentGroups.reduce(
    (sum, group) => sum + group.documents.length,
    0,
  );

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">审核控制台</h1>
            <p className="page-subtitle">集中处理交付文件、退款确认和知识库进化任务。先处理风险，再处理交付。</p>
          </div>
          <ReviewConsoleLiveRefresh />
        </div>
      </div>

      <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950 text-white shadow-sm">
        <div className="grid divide-y divide-slate-800 md:grid-cols-[1.2fr_2fr] md:divide-x md:divide-y-0">
          <div className="p-5">
            <div className="text-sm font-bold text-cyan-300">审核工作队列</div>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              风险确认与知识候选集中在这里处理。先消除高风险，再推进文件交付。
            </p>
          </div>
          <div className="grid grid-cols-3 divide-x divide-slate-800">
            <ReviewMetric label="退款确认" value={refundPendingCases.length} tone="risk" />
            <ReviewMetric label="文件待审核" value={pendingDocumentCount} tone="review" />
            <ReviewMetric label="知识/规则候选" value={pendingKnowledgeDrafts.length} tone="info" />
          </div>
        </div>
      </section>

      {/* 置顶全局开关 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <AutoSendToggle initialValue={policy.mode === "auto_send"} />
        <AiReplyToggle
          initialValue={aiReplyEnabled.enabled}
          autoDisabledReason={aiReplyEnabled.auto_disabled_reason}
          autoDisabledAt={aiReplyEnabled.auto_disabled_at}
        />
      </div>

      {/* 1. 交付文件控制台 */}
      <DocumentSearchList />

      {/* 2. 退款审核 */}
      <RefundReviewGroup cases={refundPendingCases} />

      {/* 3. 参考问答库 (FAQ) 进化工作台 */}
      <FAQEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
        indexStats={knowledgeIndexStats}
        faqs={knowledgeFaqs}
        evolutionConfig={knowledgeEvolutionConfig}
      />

      {/* 4. Chat Agent 回答规则纠正 */}
      <AgentRuleEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
      />

      {/* 5. 优秀标杆案例库进化工作台 */}
      <ExcellentCaseEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        approvedDrafts={approvedKnowledgeDrafts}
        evolutionConfig={knowledgeEvolutionConfig}
      />
    </div>
  );
}


function ReviewMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "risk" | "review" | "info";
}) {
  const toneClass: Record<typeof tone, string> = {
    risk: "text-rose-300",
    review: "text-amber-300",
    info: "text-cyan-300",
  };
  return (
    <div className="p-4 sm:p-5">
      <div className={`text-3xl font-black tracking-tight ${toneClass[tone]}`}>{value}</div>
      <div className="mt-2 text-xs font-bold text-slate-300">{label}</div>
    </div>
  );
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
      <div className="panel-body">
        {cases.length > 0 ? (
          <div className="space-y-4">
            {cases.map((item) => {
              const evidence = latestRefundEvidence(item.refund_intent_flags);
              return (
                <div key={item.id} className="flex flex-col gap-3 rounded-xl border border-rose-200 bg-white px-4 py-4 transition-colors hover:bg-rose-50/30 sm:flex-row sm:items-start sm:justify-between sm:px-5">
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
          <div className="empty-state">
            <CheckCircle2 className="mx-auto mb-3 h-9 w-9 text-emerald-500" />
            <p className="text-sm font-bold">暂无待审核退款</p>
          </div>
        )}
      </div>
    </section>
  );
}
