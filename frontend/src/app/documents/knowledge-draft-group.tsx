"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  KnowledgeDraft,
  KnowledgeIndexStats,
  KnowledgeFAQ,
} from "@/lib/api";
import {
  KnowledgeDraftActions,
  KnowledgeFAQEditor,
} from "./knowledge-draft-actions";
import {
  BookOpen,
  Award,
  Compass,
  Sparkles,
  Inbox,
  Library,
  LoaderCircle,
} from "lucide-react";

/** 已入库案例每页条数：单条约 2.5KB，30 条≈75KB，够看一屏又不拖慢首屏。 */
const CASE_PAGE_SIZE = 30;

export function FAQEvolutionCenter({
  pendingDrafts,
  indexStats,
}: {
  pendingDrafts: KnowledgeDraft[];
  indexStats: KnowledgeIndexStats;
}) {
  const qaPending = pendingDrafts.filter((d) => d.draft_type === "qa");
  const [activeTab, setActiveTab] = useState<"review" | "library">(
    qaPending.length > 0 ? "review" : "library",
  );
  // FAQ 全量约 170KB，过去随首屏 SSR 下发；现改为打开"FAQ 库"页签时才取。
  const [faqs, setFaqs] = useState<KnowledgeFAQ[] | null>(null);
  const [faqsLoading, setFaqsLoading] = useState(false);
  const [faqsError, setFaqsError] = useState<string | null>(null);

  const loadFaqs = useCallback(async () => {
    setFaqsLoading(true);
    setFaqsError(null);
    try {
      // 编辑器要按检索中/已停用/已归档三态本地切换，所以这里取全量而不分页。
      setFaqs(await api<KnowledgeFAQ[]>("/knowledge-faqs?status=all"));
    } catch (err) {
      console.error("load knowledge faqs failed", err);
      setFaqsError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setFaqsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== "library") return;
    if (faqs !== null || faqsLoading || faqsError) return;
    loadFaqs();
  }, [activeTab, faqs, faqsLoading, faqsError, loadFaqs]);

  const activeFaqCount = faqs?.filter((faq) => faq.status === "active").length ?? null;

  function draftSource(draft: KnowledgeDraft) {
    return draft.content_json?.source === "archivist_staff_correction"
      ? "班主任纠偏"
      : "群聊问答";
  }

  return (
    <section className="animate-fadeIn overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-6 py-5">
        <div className="flex items-start gap-3">
          <BookOpen className="mt-0.5 h-6 w-6 shrink-0 text-blue-650" />
          <div>
            <h2 className="text-lg font-bold text-slate-900">参考问答库（FAQ）</h2>
            <p className="mt-1 max-w-3xl text-sm leading-relaxed text-slate-600">
              档案官从群聊中提炼候选问答；人工确认后进入 FAQ 库并参与检索。
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-b border-slate-200 bg-slate-50 px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1" role="tablist" aria-label="FAQ 工作区">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "review"}
            aria-controls="faq-review-panel"
            onClick={() => setActiveTab("review")}
            className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-bold transition-colors ${
              activeTab === "review"
                ? "bg-white text-blue-700 shadow-sm"
                : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
            }`}
          >
            <Inbox className="h-4 w-4" />
            <span>待审核</span>
            <span className="tabular-nums text-slate-500">{qaPending.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "library"}
            aria-controls="faq-library-panel"
            onClick={() => setActiveTab("library")}
            className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-bold transition-colors ${
              activeTab === "library"
                ? "bg-white text-blue-700 shadow-sm"
                : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
            }`}
          >
            <Library className="h-4 w-4" />
            <span>FAQ 库</span>
            <span className="tabular-nums text-slate-500">
              {activeFaqCount ?? "—"}
            </span>
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-600">
          <span>
            全库可检索
            <strong className="ml-1 tabular-nums text-slate-900">{indexStats.total_chunks}</strong>
          </span>
          <span>
            向量就绪
            <strong className="ml-1 tabular-nums text-slate-900">{indexStats.embedded_chunks}</strong>
          </span>
        </div>
      </div>

      <div className="bg-white p-6">
        {activeTab === "review" ? (
          <div
            id="faq-review-panel"
            role="tabpanel"
            className="mx-auto w-full max-w-5xl animate-fadeIn"
          >
            <div className="mb-5 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900">待确认的参考问答</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  在这里核对问题、回复口径和适用范围。批准后会自动进入 FAQ 库并建立向量。
                </p>
              </div>
              <span className="text-xs font-semibold text-slate-500">
                {qaPending.length} 条待处理
              </span>
            </div>

            {qaPending.length > 0 ? (
              <div className="max-h-[720px] space-y-5 overflow-y-auto pr-1.5">
                {qaPending.map((draft) => (
                  <div
                    key={draft.id}
                    className="rounded-xl border border-slate-200 bg-white p-5 transition-colors hover:border-slate-300"
                  >
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-md border border-blue-100 bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
                          参考问答
                        </span>
                        <span className="rounded-md border border-violet-100 bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700">
                          {draftSource(draft)}
                        </span>
                        {draft.category && (
                          <span className="rounded-md border border-slate-200/50 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                            {draft.category}
                          </span>
                        )}
                      </div>
                      <span className="text-xs font-medium text-slate-400">{draft.created_at}</span>
                    </div>

                    {draft.reason && (
                      <div className="mb-3 rounded-lg border border-amber-100/60 bg-amber-50/40 p-2.5 text-xs text-amber-950">
                        <span className="font-semibold text-amber-800">推荐原因：</span>
                        {draft.reason}
                      </div>
                    )}
                    {draft.evidence && (
                      <div className="mb-4 rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
                        <span className="mb-1 block font-semibold text-slate-500">聊天证据：</span>
                        <div className="whitespace-pre-wrap">{draft.evidence}</div>
                      </div>
                    )}

                    <KnowledgeDraftActions
                      draftId={draft.id}
                      draftType="qa"
                      // 批准后新 FAQ 要出现在已加载的 FAQ 库里；没加载过则不必白跑一次请求。
                      onMutated={faqs ? loadFaqs : undefined}
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
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/40 py-16 text-center text-slate-500">
                <Inbox className="mx-auto mb-3 h-8 w-8 text-slate-400" />
                <p className="text-sm font-semibold">暂无待审核 FAQ 候选</p>
                <p className="mt-1 text-xs text-slate-400">档案官尚未提炼出需要人工确认的新问答。</p>
              </div>
            )}
          </div>
        ) : (
          <div
            id="faq-library-panel"
            role="tabpanel"
            className="mx-auto w-full max-w-5xl animate-fadeIn"
          >
            {faqs ? (
              <KnowledgeFAQEditor faqs={faqs} onChanged={loadFaqs} />
            ) : faqsError ? (
              <LazyLoadError message={`FAQ 库加载失败：${faqsError}`} onRetry={loadFaqs} />
            ) : (
              <LazyLoadPlaceholder label="正在加载 FAQ 库…" />
            )}
          </div>
        )}
      </div>
    </section>
  );
}

export function ExcellentCaseEvolutionCenter({
  pendingDrafts,
}: {
  pendingDrafts: KnowledgeDraft[];
}) {
  const casePending = pendingDrafts.filter((d) => d.draft_type === "case");
  const [activeTab, setActiveTab] = useState<"review" | "library">(
    casePending.length > 0 ? "review" : "library",
  );
  // 已入库案例过去随首屏一次性下发（226 条 ≈568KB）；现改为打开"案例库"页签后分页取。
  const [caseApproved, setCaseApproved] = useState<KnowledgeDraft[] | null>(null);
  const [approvedTotal, setApprovedTotal] = useState<number | null>(null);
  const [casesLoading, setCasesLoading] = useState(false);
  const [casesError, setCasesError] = useState<string | null>(null);

  const loadCases = useCallback(async (reset: boolean) => {
    setCasesLoading(true);
    setCasesError(null);
    try {
      const offset = reset ? 0 : (caseApproved?.length ?? 0);
      const [page, count] = await Promise.all([
        api<KnowledgeDraft[]>(
          `/knowledge-drafts?status=approved&draft_type=case&limit=${CASE_PAGE_SIZE}&offset=${offset}`,
        ),
        api<{ total: number }>("/knowledge-drafts/count?status=approved&draft_type=case"),
      ]);
      setApprovedTotal(count.total);
      setCaseApproved((current) => (reset || current === null ? page : [...current, ...page]));
    } catch (err) {
      console.error("load approved case drafts failed", err);
      setCasesError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setCasesLoading(false);
    }
  }, [caseApproved]);

  useEffect(() => {
    if (activeTab !== "library") return;
    if (caseApproved !== null || casesLoading || casesError) return;
    loadCases(true);
  }, [activeTab, caseApproved, casesLoading, casesError, loadCases]);

  const loadedCount = caseApproved?.length ?? 0;
  const hasMore = approvedTotal !== null && loadedCount < approvedTotal;

  return (
    <section className="animate-fadeIn overflow-hidden rounded-xl border border-cyan-200 bg-white shadow-sm">
      <div className="border-b border-cyan-100 bg-cyan-50/30 px-6 py-5">
        <div className="flex items-start gap-2.5">
          <Award className="mt-0.5 h-6 w-6 shrink-0 text-cyan-700" />
          <div>
            <h2 className="text-lg font-bold text-slate-900">优秀标杆案例库</h2>
            <p className="mt-1 text-sm text-slate-500 leading-relaxed">
              档案官随群聊增量识别可复用的坚持与改善案例，人工审核后进入案例库。
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-b border-cyan-100 bg-cyan-50/20 px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1" role="tablist" aria-label="优秀案例工作区">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "review"}
            aria-controls="case-review-panel"
            onClick={() => setActiveTab("review")}
            className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-bold transition-colors ${
              activeTab === "review"
                ? "bg-white text-cyan-800 shadow-sm"
                : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
            }`}
          >
            <Inbox className="h-4 w-4" />
            <span>待审核</span>
            <span className="tabular-nums text-slate-500">{casePending.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "library"}
            aria-controls="case-library-panel"
            onClick={() => setActiveTab("library")}
            className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-bold transition-colors ${
              activeTab === "library"
                ? "bg-white text-cyan-800 shadow-sm"
                : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
            }`}
          >
            <Library className="h-4 w-4" />
            <span>案例库</span>
            <span className="tabular-nums text-slate-500">{approvedTotal ?? "—"}</span>
          </button>
        </div>
        <div className="max-w-xl text-xs font-medium leading-relaxed text-cyan-900">
          <span className="font-bold">按需检索：</span>
          用户明显受挫、自我怀疑、动力不足或想放弃时，回复 Agent 才会按需检索一条相关案例。
        </div>
      </div>

      <div className="bg-white p-6">
        {activeTab === "review" ? (
          <div
            id="case-review-panel"
            role="tabpanel"
            className="mx-auto w-full max-w-5xl animate-fadeIn"
          >
            <div className="mb-5 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900">待确认的优秀案例</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  核对案例背景、真实证据和可复用价值。批准后才会进入案例库。
                </p>
              </div>
              <span className="text-xs font-semibold text-slate-500">
                {casePending.length} 条待处理
              </span>
            </div>

            {casePending.length > 0 ? (
              <div className="max-h-[720px] space-y-5 overflow-y-auto pr-1.5">
                {casePending.map((draft) => (
                  <div
                    key={draft.id}
                    className="rounded-xl border border-slate-200 bg-white p-5 transition-colors hover:border-slate-300"
                  >
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-md border border-cyan-100 bg-cyan-50 px-2 py-0.5 text-xs font-semibold text-cyan-800">
                          优秀案例
                        </span>
                        {draft.category && (
                          <span className="rounded-md border border-slate-200/50 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                            {draft.category}
                          </span>
                        )}
                      </div>
                      <span className="text-xs font-medium text-slate-400">{draft.created_at}</span>
                    </div>

                    {draft.reason && (
                      <div className="mb-3 rounded-lg border border-amber-100/60 bg-amber-50/40 p-2.5 text-xs text-amber-950">
                        <span className="font-semibold text-amber-800">入选原因：</span>
                        {draft.reason}
                      </div>
                    )}
                    {draft.evidence && (
                      <div className="mb-4 rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
                        <span className="mb-1 block font-semibold text-slate-500">聊天证据：</span>
                        <div className="whitespace-pre-wrap">{draft.evidence}</div>
                      </div>
                    )}

                    <KnowledgeDraftActions
                      draftId={draft.id}
                      draftType="case"
                      onMutated={caseApproved ? () => loadCases(true) : undefined}
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
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/40 py-16 text-center text-slate-500">
                <Award className="mx-auto mb-3 h-8 w-8 text-cyan-700" />
                <p className="text-sm font-semibold">暂无待审核优秀案例</p>
                <p className="mt-1 text-xs text-slate-400">档案官尚未提炼出需要人工确认的新案例。</p>
              </div>
            )}
          </div>
        ) : (
          <div
            id="case-library-panel"
            role="tabpanel"
            className="mx-auto w-full max-w-5xl animate-fadeIn"
          >
            <div className="mb-5 flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-sm font-bold text-slate-900">已入库优秀案例</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  这里只展示经人工批准、可供回复 Agent 检索的案例。
                </p>
              </div>
              <span className="rounded-md border border-purple-100 bg-purple-50 px-2 py-1 text-xs font-semibold text-purple-700">
                {approvedTotal === null
                  ? "正在统计…"
                  : hasMore
                    ? `已显示 ${loadedCount} / ${approvedTotal} 个案例`
                    : `${approvedTotal} 个案例`}
              </span>
            </div>

            {caseApproved === null ? (
              casesError ? (
                <LazyLoadError
                  message={`案例库加载失败：${casesError}`}
                  onRetry={() => loadCases(true)}
                />
              ) : (
                <LazyLoadPlaceholder label="正在加载已入库案例…" />
              )
            ) : (
              <div className="max-h-[620px] space-y-4 overflow-y-auto pr-1">
                {caseApproved.map((draft) => (
                  <ExcellentCaseCard key={draft.id} draft={draft} />
                ))}
                {caseApproved.length === 0 && (
                  <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/20 py-12 text-center text-sm text-slate-400">
                    暂无已入库优秀案例。
                  </div>
                )}

                {casesError && (
                  <p
                    role="alert"
                    className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700"
                  >
                    加载更多失败：{casesError}
                  </p>
                )}

                {hasMore && (
                  <button
                    type="button"
                    onClick={() => loadCases(false)}
                    disabled={casesLoading}
                    aria-busy={casesLoading || undefined}
                    className="inline-flex min-h-9 w-full items-center justify-center gap-1.5 rounded-lg border border-cyan-200 bg-white px-3 text-xs font-bold text-cyan-800 transition-all hover:border-cyan-300 hover:bg-cyan-50 active:scale-[0.99] disabled:opacity-50"
                  >
                    {casesLoading && (
                      <LoaderCircle
                        className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                        aria-hidden="true"
                      />
                    )}
                    {casesLoading
                      ? "正在加载…"
                      : `加载更多（还有 ${approvedTotal! - loadedCount} 条）`}
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function LazyLoadPlaceholder({ label }: { label: string }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50/40 py-16 text-center"
      aria-busy="true"
    >
      <LoaderCircle
        className="h-6 w-6 animate-spin text-cyan-700 motion-reduce:animate-none"
        aria-hidden="true"
      />
      <p className="text-sm font-semibold text-slate-600" role="status" aria-live="polite">
        {label}
      </p>
    </div>
  );
}

function LazyLoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-xl border border-rose-200 bg-rose-50/60 py-12 text-center"
    >
      <p className="px-4 text-sm font-semibold text-rose-800">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex min-h-9 items-center rounded-lg border border-rose-300 bg-white px-3.5 text-xs font-bold text-rose-800 transition-colors hover:bg-rose-100"
      >
        重试
      </button>
    </div>
  );
}

function ExcellentCaseCard({ draft }: { draft: KnowledgeDraft }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="rounded-xl border border-cyan-100 bg-cyan-50/10 p-4 shadow-3xs hover:border-cyan-200 hover:bg-cyan-50/20 transition-all duration-200">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="rounded-md bg-cyan-100 text-cyan-800 px-1.5 py-0.5 text-2xs font-bold border border-cyan-200/60">
              标杆案例
            </span>
            {draft.category && (
              <span className="rounded-md bg-slate-100 text-slate-650 px-1.5 py-0.5 text-2xs font-semibold border border-slate-200/50">
                {draft.category}
              </span>
            )}
          </div>
          <h4 className="text-sm font-bold text-slate-800 leading-snug pt-1">{draft.title}</h4>
        </div>
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="text-xs font-semibold text-purple-600 hover:text-purple-700 shrink-0 select-none outline-none"
        >
          {isOpen ? "收起详情" : "展开详情"}
        </button>
      </div>

      <div className={`space-y-3.5 overflow-hidden transition-all duration-300 ${isOpen ? "max-h-[1000px] mt-4 pt-3.5 border-t border-purple-100/50" : "max-h-0"}`}>
        <div className="space-y-1">
          <div className="text-[10px] font-bold text-purple-700/60 uppercase tracking-wider flex items-center gap-1">
            <Compass className="h-3 w-3" />
            <span>案例适用场景与背景</span>
          </div>
          <p className="text-xs text-slate-650 leading-relaxed font-normal whitespace-pre-wrap pl-2.5 border-l border-purple-200">
            {draft.question}
          </p>
        </div>

        <div className="space-y-1">
          <div className="text-[10px] font-bold text-amber-700/60 uppercase tracking-wider flex items-center gap-1">
            <Sparkles className="h-3 w-3" />
            <span>金牌辅导过程与成效</span>
          </div>
          <p className="text-xs text-slate-700 leading-relaxed font-normal whitespace-pre-wrap pl-2.5 border-l border-amber-250">
            {draft.answer}
          </p>
        </div>

        {draft.reason && (
          <div className="text-2xs text-slate-500 bg-slate-50 border border-slate-100 p-2 rounded-lg leading-relaxed">
            <span className="font-semibold text-slate-700">采纳要点：</span>
            {draft.reason}
          </div>
        )}
      </div>

      {draft.tags && draft.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100/50 pt-2.5">
          {draft.tags.map((tag, idx) => (
            <span
              key={idx}
              className="rounded-full bg-purple-50/55 text-purple-650 border border-purple-100/40 px-2 py-0.5 text-3xs font-semibold"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
