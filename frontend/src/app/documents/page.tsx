import Link from "next/link";
import {
  api,
  ServiceCase,
  KnowledgeDraft,
  KnowledgeIndexStats,
  DashboardCasePendingDocuments,
} from "@/lib/api";
import { AutoSendToggle } from "./auto-send-toggle";
import { AiReplyToggle } from "./ai-reply-toggle";
import { FAQEvolutionCenter, ExcellentCaseEvolutionCenter } from "./knowledge-draft-group";
import { DocumentSearchList } from "./document-search-list";
import { ReviewConsoleLiveRefresh } from "./review-console-live-refresh";
import { RefundReviewActions } from "./refund-review-actions";
import { PlateauReviewActions } from "./plateau-review-actions";
import { CheckCircle2, Minus, RefreshCcw, TrendingUp } from "lucide-react";

export default async function DocumentsListPage() {
  // 首屏只取"审核要用"的轻量数据。知识库两块（FAQ 231 条 ≈170KB、已通过案例 226 条 ≈568KB）
  // 曾在这里同步取并 SSR 进首屏，把控制台顶成 1.68MB / 4 秒；现改为组件内按需加载。
  const [
    policy,
    aiReplyEnabled,
    refundPendingCases,
    plateauFollowupCases,
    pendingDocumentGroups,
    pendingKnowledgeDrafts,
    knowledgeIndexStats,
  ] = await Promise.all([
    api<{ mode: string }>("/settings/content-delivery-policy").catch(() => ({ mode: "manual_review" })),
    api<{ enabled: boolean; auto_disabled_reason: string | null; auto_disabled_at: string | null }>(
      "/settings/ai-reply-enabled",
    ).catch(() => ({ enabled: true, auto_disabled_reason: null, auto_disabled_at: null })),
    api<ServiceCase[]>("/service-cases?filter=refund_pending").catch(() => []),
    api<ServiceCase[]>("/service-cases?filter=plateau_followup").catch(() => []),
    api<DashboardCasePendingDocuments[]>("/dashboard/pending-documents").catch(() => []),
    api<KnowledgeDraft[]>("/knowledge-drafts?status=pending").catch(() => []),
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
          <div className="grid grid-cols-2 divide-x divide-y divide-slate-800 sm:grid-cols-4 sm:divide-y-0">
            <ReviewMetric label="退款确认" value={refundPendingCases.length} tone="risk" />
            <ReviewMetric label="体重状态跟进" value={plateauFollowupCases.length} tone="risk" />
            <ReviewMetric label="文件待审核" value={pendingDocumentCount} tone="review" />
            <ReviewMetric label="知识候选" value={pendingKnowledgeDrafts.length} tone="info" />
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

      {/* 1. 交付文件控制台（首屏用服务端已取的同一份数据直出，避免再空转一次骨架） */}
      <DocumentSearchList initialPendingGroups={pendingDocumentGroups} />

      {/* 2. 退款审核 */}
      <RefundReviewGroup cases={refundPendingCases} />

      {/* 3. 体重状态跟进（平台期/反弹） */}
      <PlateauFollowupGroup cases={plateauFollowupCases} />

      {/* 4. 参考问答库 (FAQ) 进化工作台（FAQ 库内容按需加载） */}
      <FAQEvolutionCenter
        pendingDrafts={pendingKnowledgeDrafts}
        indexStats={knowledgeIndexStats}
      />

      {/* 5. 优秀标杆案例库进化工作台（已入库案例按需分页加载） */}
      <ExcellentCaseEvolutionCenter pendingDrafts={pendingKnowledgeDrafts} />
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

/** 判定依据里那句人话说明（窗口区间、趋势、数据够不够），由后端给，前端不复算业务规则。 */
function plateauNote(evidence?: Record<string, unknown> | null): string {
  return typeof evidence?.note === "string" ? evidence.note : "";
}

function plateauDeclaredQuote(evidence?: Record<string, unknown> | null): string {
  const declared = Array.isArray(evidence?.declared_evidence) ? evidence.declared_evidence : [];
  return typeof declared[0] === "string" ? declared[0] : "";
}

function PlateauFollowupGroup({ cases }: { cases: ServiceCase[] }) {
  return (
    <section id="plateau-followups" className="panel overflow-hidden scroll-mt-6">
      <div className="panel-header">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="panel-title">体重状态跟进</h2>
          <span className="status-pill border-violet-200 bg-violet-50 text-violet-700">
            {cases.length} 单待跟进
          </span>
        </div>
        <p className="panel-subtitle">
          每天中午扫出的平台期与体重反弹。平台期建议调整方案，反弹更该尽快介入。跟进后点「标记已跟进」即从这里消失；
          之后如果结论又变了（离开平台期又回来、平台期转成反弹），会自动重新出现。
        </p>
      </div>
      <div className="panel-body">
        {cases.length > 0 ? (
          <div className="space-y-4">
            {cases.map((item) => {
              const rebound = item.plateau_status === "rebound";
              const note = plateauNote(item.plateau_evidence);
              const quote = plateauDeclaredQuote(item.plateau_evidence);
              return (
                <div
                  key={item.id}
                  className={`flex flex-col gap-3 rounded-xl border bg-white px-4 py-4 transition-colors sm:flex-row sm:items-start sm:justify-between sm:px-5 ${
                    rebound ? "border-rose-200 hover:bg-rose-50/30" : "border-violet-200 hover:bg-violet-50/30"
                  }`}
                >
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-semibold ${
                          rebound
                            ? "border-rose-300 bg-rose-100 text-rose-700"
                            : "border-violet-300 bg-violet-100 text-violet-700"
                        }`}
                      >
                        {rebound ? <TrendingUp className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
                        {rebound ? "体重反弹" : "平台期"}
                      </span>
                      <Link className="text-break font-bold text-cyan-700 hover:underline" href={`/cases/${item.id}`}>
                        {item.case_no}
                      </Link>
                      {item.user?.name && <span className="font-semibold text-slate-600">· {item.user.name}</span>}
                      {item.group_id && <span className="text-slate-400">· {item.group_id}</span>}
                    </div>
                    {note && <p className="text-break text-sm leading-relaxed text-slate-700">{note}</p>}
                    {quote && (
                      <p className="text-break text-sm leading-relaxed text-slate-700">
                        <span className="font-bold text-slate-400">客户说：</span>
                        {quote}
                      </p>
                    )}
                  </div>
                  <PlateauReviewActions caseId={item.id} />
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">
            <CheckCircle2 className="mx-auto mb-3 h-9 w-9 text-emerald-500" />
            <p className="text-sm font-bold">暂无待跟进的平台期或反弹</p>
          </div>
        )}
      </div>
    </section>
  );
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
