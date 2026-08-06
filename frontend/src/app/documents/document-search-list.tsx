"use client";

import { useState, useEffect, useMemo, useRef, useCallback, type MouseEvent } from "react";
import Link, { useLinkStatus } from "next/link";
import { api, UserDocument, DashboardCasePendingDocuments, API_BASE } from "@/lib/api";
import { useSSE } from "@/hooks/use-sse";
import {
  FileText,
  Search,
  RefreshCw,
  Eye,
  ArrowRight,
  Clock,
  ShieldCheck,
  ShieldAlert,
  X,
  User,
  Phone,
  FileCheck2,
  LoaderCircle,
} from "lucide-react";

type ReviewDocument = {
  id: number;
  service_case_id: number;
  document_type: string;
  title: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  case_no: string;
  group_id: string;
  user_name: string;
  user_phone: string;
  /** 内部运营状态：需人工复核的入营文件不会自动发送，必须运营检查后手动确认发送。 */
  manual_review_required?: boolean;
  manual_review_reasons?: string[];
};

/** 返回审核控制台时用来还原滚动位置（页面 + 卡片区两层）。 */
const REVIEW_SCROLL_KEY = "review-console-scroll";

export function DocumentSearchList({
  initialPendingGroups,
}: {
  /** 服务端已为顶部计数取过同一份数据，直接传进来：省一次请求，返回本页时也不再空转骨架。 */
  initialPendingGroups?: DashboardCasePendingDocuments[];
}) {
  const [pendingDocuments, setPendingDocuments] = useState<ReviewDocument[]>(() =>
    initialPendingGroups ? flattenPendingDocuments(initialPendingGroups) : [],
  );
  const [historyDocuments, setHistoryDocuments] = useState<ReviewDocument[]>([]);
  const [loadingPending, setLoadingPending] = useState(!initialPendingGroups);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "reviewed">("pending");
  const [refreshing, setRefreshing] = useState(false);
  const { pendingDocuments: pendingDocumentCount } = useSSE();
  const lastPendingDocuments = useRef<number | null>(null);
  const pendingListRef = useRef<HTMLDivElement | null>(null);
  const scrollRestored = useRef(false);

  /**
   * 点卡片进详情前记下位置：这一页有两层滚动（页面本身 + 待审卡片区自己的滚动条），
   * 只还原页面滚动仍会把运营丢回卡片区顶部，所以两层都要记。
   */
  const rememberScrollPosition = useCallback(() => {
    try {
      sessionStorage.setItem(
        REVIEW_SCROLL_KEY,
        JSON.stringify({
          page: window.scrollY,
          list: pendingListRef.current?.scrollTop ?? 0,
          at: Date.now(),
        }),
      );
    } catch {
      // 隐私模式等禁用 sessionStorage 时放弃记录，不影响跳转本身
    }
  }, []);

  // 返回本页时还原到刚才那张卡片；从侧边栏正常进入时不还原（进入即消耗掉标记）。
  useEffect(() => {
    if (scrollRestored.current || pendingDocuments.length === 0) return;
    let raw: string | null = null;
    try {
      raw = sessionStorage.getItem(REVIEW_SCROLL_KEY);
      if (raw) sessionStorage.removeItem(REVIEW_SCROLL_KEY);
    } catch {
      return;
    }
    scrollRestored.current = true;
    if (!raw) return;
    let saved: { page?: number; list?: number; at?: number };
    try {
      saved = JSON.parse(raw);
    } catch {
      return;
    }
    // 隔太久（比如第二天翻到这个标签页）就不要莫名其妙地跳到半页中间。
    if (!saved.at || Date.now() - saved.at > 10 * 60 * 1000) return;
    // 双帧：等路由自己的滚动处理跑完再定位，否则会被重置回顶部。
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (pendingListRef.current && saved.list) {
          pendingListRef.current.scrollTop = saved.list;
        }
        if (saved.page) window.scrollTo({ top: saved.page, behavior: "auto" });
      });
    });
  }, [pendingDocuments.length]);

  const fetchPendingDocuments = useCallback(async () => {
    try {
      const grouped = await api<DashboardCasePendingDocuments[]>("/dashboard/pending-documents");
      setPendingDocuments(flattenPendingDocuments(grouped));
    } catch (err) {
      console.error("Failed to fetch pending documents:", err);
    } finally {
      setLoadingPending(false);
      setRefreshing(false);
    }
  }, []);

  const fetchHistoryDocuments = useCallback(async () => {
    if (loadingHistory) return;
    setLoadingHistory(true);
    try {
      const [rawDocs, cases] = await Promise.all([
        api<UserDocument[]>("/documents"),
        api<any[]>("/service-cases"),
      ]);
      const casesMap = new Map(cases.map((c) => [c.id, c]));
      const mapped: ReviewDocument[] = rawDocs.map((doc) => {
        const sc = casesMap.get(doc.service_case_id);
        return {
          id: doc.id,
          service_case_id: doc.service_case_id,
          document_type: doc.document_type,
          title: doc.title,
          status: doc.status,
          case_no: sc?.case_no || "未知服务单",
          group_id: sc?.group_id || "未知群组",
          user_name: sc?.user?.name || "未知客户",
          user_phone: sc?.user?.phone || "无手机号",
        };
      });
      setHistoryDocuments(mapped.filter((doc) => doc.status === "approved" || doc.status === "sent" || doc.status === "archived"));
      setHistoryLoaded(true);
    } catch (err) {
      console.error("Failed to fetch document history:", err);
    } finally {
      setLoadingHistory(false);
    }
  }, [loadingHistory]);

  useEffect(() => {
    fetchPendingDocuments();
  }, [fetchPendingDocuments]);

  useEffect(() => {
    if (lastPendingDocuments.current === null) {
      lastPendingDocuments.current = pendingDocumentCount;
      return;
    }
    if (lastPendingDocuments.current !== pendingDocumentCount) {
      lastPendingDocuments.current = pendingDocumentCount;
      fetchPendingDocuments();
    }
  }, [fetchPendingDocuments, pendingDocumentCount]);

  useEffect(() => {
    if ((statusFilter === "all" || statusFilter === "reviewed") && !historyLoaded) {
      fetchHistoryDocuments();
    }
  }, [fetchHistoryDocuments, statusFilter, historyLoaded]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchPendingDocuments();
    if (historyLoaded) {
      fetchHistoryDocuments();
    }
  };

  const statusConfig: Record<string, { label: string; className: string }> = {
    reviewing: { label: "待审核", className: "bg-amber-50 text-amber-700 border-amber-200/90" },
    approved: { label: "已批准", className: "bg-emerald-50 text-emerald-700 border-emerald-200/90" },
    sent: { label: "已发送", className: "bg-cyan-50 text-cyan-800 border-cyan-200/90" },
    draft: { label: "草稿", className: "bg-slate-100 text-slate-600 border-slate-200" },
    archived: { label: "未发送", className: "bg-slate-100 text-slate-500 border-slate-300" },
  };

  // Client-side fuzzy search and status filter
  const filteredDocuments = useMemo(() => {
    const documents = statusFilter === "pending"
      ? pendingDocuments
      : statusFilter === "reviewed"
        ? historyDocuments
        : [...pendingDocuments, ...historyDocuments];
    return documents.filter((doc) => {
      const query = searchQuery.trim().toLowerCase();
      const matchesSearch =
        !query ||
        doc.title.toLowerCase().includes(query) ||
        doc.user_name.toLowerCase().includes(query) ||
        doc.user_phone.toLowerCase().includes(query) ||
        doc.case_no.toLowerCase().includes(query);

      if (!matchesSearch) return false;
      if (statusFilter === "pending") {
        return doc.status === "reviewing" || doc.status === "draft";
      }
      if (statusFilter === "reviewed") {
        return doc.status === "approved" || doc.status === "sent" || doc.status === "archived";
      }
      return true;
    });
  }, [pendingDocuments, historyDocuments, searchQuery, statusFilter]);

  const pendingCount = pendingDocuments.length;
  const reviewedCount = historyDocuments.length;
  const allCountLabel = historyLoaded ? String(pendingCount + reviewedCount) : `${pendingCount}+`;

  if (loadingPending) {
    return (
      <div className="space-y-6">
        <div className="panel flex animate-pulse flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="h-11 w-full rounded-xl bg-slate-100 sm:w-80" />
          <div className="h-11 w-full rounded-xl bg-slate-100 sm:w-72" />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="panel space-y-4 p-5">
            <div className="h-6 w-32 animate-pulse rounded bg-slate-100" />
            <div className="h-36 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
            <div className="h-36 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
          </div>
          <div className="panel space-y-4 p-5">
            <div className="h-6 w-32 animate-pulse rounded bg-slate-100" />
            <div className="h-36 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
            <div className="h-36 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
          </div>
        </div>
      </div>
    );
  }

  const pendingDocs = filteredDocuments.filter((doc) => doc.status === "reviewing" || doc.status === "draft");
  const reviewedDocs = filteredDocuments.filter((doc) => doc.status === "approved" || doc.status === "sent" || doc.status === "archived");
  const visibleDocuments = statusFilter === "pending"
    ? pendingDocuments
    : statusFilter === "reviewed"
      ? historyDocuments
      : [...pendingDocuments, ...historyDocuments];

  return (
    <div className="space-y-6">
      {/* 搜索与筛选控制栏 */}
      <div className="panel relative flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
        <div className="relative min-w-0 flex-1 sm:max-w-lg">
          <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="搜索文档名称、客户姓名、电话、服务单号..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-11 w-full rounded-xl border border-slate-200/90 bg-slate-50/70 pl-10 pr-9 text-sm text-slate-900 transition-all placeholder:text-slate-400 hover:bg-slate-50 focus:border-cyan-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 sm:justify-end">
          <div className="inline-flex rounded-xl border border-slate-200/90 bg-slate-100/80 p-1 shadow-inner">
            <button
              onClick={() => setStatusFilter("all")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-bold transition-all ${
                statusFilter === "all"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <span>全部</span>
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[11px] tabular ${
                  statusFilter === "all" ? "bg-slate-100 text-slate-800" : "bg-slate-200/70 text-slate-600"
                }`}
              >
                {allCountLabel}
              </span>
            </button>
            <button
              onClick={() => setStatusFilter("pending")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-bold transition-all ${
                statusFilter === "pending"
                  ? "bg-white text-amber-900 shadow-sm"
                  : "text-slate-600 hover:text-amber-900"
              }`}
            >
              <span>待审核</span>
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[11px] tabular ${
                  statusFilter === "pending" ? "bg-amber-100 text-amber-800" : "bg-amber-100/60 text-amber-700"
                }`}
              >
                {pendingCount}
              </span>
            </button>
            <button
              onClick={() => setStatusFilter("reviewed")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-bold transition-all ${
                statusFilter === "reviewed"
                  ? "bg-white text-emerald-900 shadow-sm"
                  : "text-slate-600 hover:text-emerald-900"
              }`}
            >
              <span>已审核</span>
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[11px] tabular ${
                  statusFilter === "reviewed" ? "bg-emerald-100 text-emerald-800" : "bg-emerald-100/60 text-emerald-700"
                }`}
              >
                {historyLoaded ? reviewedCount : "..."}
              </span>
            </button>
          </div>

          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 shadow-xs transition-all hover:border-cyan-300 hover:bg-cyan-50/50 hover:text-cyan-800 active:scale-95 disabled:opacity-50"
            title="刷新文档"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin text-cyan-700" : ""}`} />
          </button>
        </div>
      </div>

      {visibleDocuments.length === 0 && statusFilter === "pending" ? (
        <div className="empty-state sm:p-12">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white shadow-xs">
            <FileCheck2 className="h-6 w-6 text-slate-400" />
          </div>
          <p className="text-base font-bold text-slate-800">暂无待审核的交付文件</p>
          <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
            客户提交入营问卷后，AI 将自动评估并生成专属 28 天交付方案。
          </p>
        </div>
      ) : (
        <div className={statusFilter === "all" ? "grid gap-6 lg:grid-cols-2" : "w-full space-y-6"}>
          {/* 第一列 / 单栏全宽：待审核列表 */}
          {(statusFilter === "all" || statusFilter === "pending") && (
            <section className="panel flex flex-col overflow-hidden">
              <div className="panel-header-clean flex items-center justify-between">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900">
                    <span className="relative flex h-2.5 w-2.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
                    </span>
                    文件待审核
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-500">新生成，需要教练二次复核并确认发送的交付件</p>
                </div>
                <span className="status-pill border-amber-200/90 bg-amber-50 font-mono text-amber-800">
                  {pendingDocs.length} 份
                </span>
              </div>

              <div className="flex flex-1 flex-col bg-slate-50/50 p-4 sm:p-5">
                {pendingDocs.length > 0 ? (
                  <div
                    ref={pendingListRef}
                    className={
                      statusFilter === "all"
                        ? "max-h-[600px] space-y-3.5 overflow-y-auto pr-1"
                        : "max-h-[650px] grid gap-4 md:grid-cols-2 overflow-y-auto pr-1 p-0.5"
                    }
                  >
                    {pendingDocs.map((doc) => (
                      <DocumentCard
                        key={doc.id}
                        doc={doc}
                        statusConfig={statusConfig}
                        onDiscard={fetchPendingDocuments}
                        onOpen={rememberScrollPosition}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="empty-state flex flex-1 flex-col items-center justify-center bg-white py-12">
                    <Clock className="mb-2 h-7 w-7 text-slate-300" />
                    <p className="text-xs font-bold text-slate-600">当前没有需要审核的文件</p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* 第二列 / 单栏全宽：已审核列表 */}
          {(statusFilter === "all" || statusFilter === "reviewed") && (
            <section className="panel flex flex-col overflow-hidden">
              <div className="panel-header-clean flex items-center justify-between">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900">
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                    已审核
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-500">已经教练确认发送、或审批归档的交付件</p>
                </div>
                <span className="status-pill border-emerald-200/90 bg-emerald-50 font-mono text-emerald-800">
                  {reviewedDocs.length} 份
                </span>
              </div>

              <div className="flex flex-1 flex-col bg-slate-50/50 p-4 sm:p-5">
                {reviewedDocs.length > 0 ? (
                  <div
                    className={
                      statusFilter === "all"
                        ? "max-h-[600px] space-y-3.5 overflow-y-auto pr-1"
                        : "max-h-[650px] grid gap-4 md:grid-cols-2 overflow-y-auto pr-1 p-0.5"
                    }
                  >
                    {reviewedDocs.map((doc) => (
                      <DocumentCard
                        key={doc.id}
                        doc={doc}
                        statusConfig={statusConfig}
                        onOpen={rememberScrollPosition}
                      />
                    ))}
                  </div>
                ) : loadingHistory ? (
                  <div className="empty-state flex flex-1 flex-col items-center justify-center bg-white py-12">
                    <RefreshCw className="mb-2 h-7 w-7 animate-spin text-cyan-600" />
                    <p className="text-xs font-bold text-slate-600">正在加载已审核历史记录...</p>
                  </div>
                ) : (
                  <div className="empty-state flex flex-1 flex-col items-center justify-center bg-white py-12">
                    <ShieldCheck className="mb-2 h-7 w-7 text-slate-300" />
                    <p className="text-xs font-bold text-slate-600">暂无已审核记录</p>
                  </div>
                )}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function flattenPendingDocuments(items: DashboardCasePendingDocuments[]): ReviewDocument[] {
  return items.flatMap((item) =>
    item.documents.map((doc) => ({
      id: doc.document_id,
      service_case_id: item.case.service_case_id,
      document_type: doc.document_type,
      title: doc.title,
      status: doc.status,
      created_at: doc.created_at,
      updated_at: doc.updated_at,
      case_no: item.case.case_no,
      group_id: item.case.group_name || item.case.group_id,
      user_name: item.case.user_name || "未知客户",
      user_phone: item.case.user_phone || "无手机号",
      manual_review_required: doc.manual_review_required,
      manual_review_reasons: doc.manual_review_reasons,
    })),
  );
}

function DocumentCard({
  doc,
  statusConfig,
  onDiscard,
  onOpen,
}: {
  doc: ReviewDocument;
  statusConfig: Record<string, { label: string; className: string }>;
  onDiscard?: () => void | Promise<void>;
  /** 进详情前记下当前滚动位置，返回时才能回到这张卡片而不是页面顶部。 */
  onOpen?: () => void;
}) {
  const [discarding, setDiscarding] = useState(false);
  const [discardError, setDiscardError] = useState<string | null>(null);
  const status = statusConfig[doc.status] || {
    label: doc.status,
    className: "bg-slate-100 text-slate-700 border-slate-200",
  };

  const isPending = doc.status === "reviewing" || doc.status === "draft";

  async function handleDiscard(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!onDiscard || discarding) return;
    if (!window.confirm(`确认不发送《${doc.title}》？将移到「已审核」列并标记为「未发送」。`)) return;
    setDiscarding(true);
    setDiscardError(null);
    try {
      const response = await fetch(`${API_BASE}/documents/${doc.id}/discard`, { method: "POST" });
      if (!response.ok) {
        throw new Error((await response.text()) || "操作失败，请稍后再试。");
      }
      await onDiscard();
    } catch (err) {
      console.error("discard document failed", err);
      setDiscardError(err instanceof Error ? err.message : "操作失败，请稍后再试。");
    } finally {
      setDiscarding(false);
    }
  }

  return (
    <Link
      href={`/documents/${doc.id}`}
      onClick={onOpen}
      className="group relative flex flex-col justify-between rounded-xl border border-slate-200/90 bg-white p-4 sm:p-5 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:border-cyan-400/80 hover:shadow-md"
    >
      <div className="space-y-3">
        {/* 卡片头部：图标 + 标题 + 状态 Pill */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-cyan-50/80 text-cyan-700 border border-cyan-100/70 transition-colors group-hover:bg-cyan-600 group-hover:text-white group-hover:border-cyan-600">
              <FileText className="h-4.5 w-4.5" />
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-bold leading-snug text-slate-900 transition-colors group-hover:text-cyan-800 text-break">
                {doc.title}
              </h3>
            </div>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <span className={`rounded-md border px-2.5 py-1 text-xs font-bold ${status.className}`}>
              {status.label}
            </span>
            {doc.manual_review_required && (
              <span className="inline-flex items-center gap-1 rounded-md border border-rose-300 bg-rose-100 px-2 py-1 text-xs font-bold text-rose-800">
                <ShieldAlert className="h-3.5 w-3.5" />
                需人工复核
              </span>
            )}
          </div>
        </div>

        {doc.manual_review_required && (
          <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold leading-relaxed text-rose-800">
            已阻止自动发送，请检查个案方案后再确认发送
            {doc.manual_review_reasons && doc.manual_review_reasons.length > 0 && (
              <span className="mt-1 block font-medium text-rose-700">
                触发原因：{doc.manual_review_reasons.join("；")}
              </span>
            )}
          </p>
        )}

        {discardError && (
          <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700" role="alert">
            未能标记为“不发送”：{discardError}
          </p>
        )}

        {/* 客户与服务单号元信息 */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1 font-bold text-slate-800">
            <User className="h-3.5 w-3.5 text-slate-400" />
            {doc.user_name}
          </span>

          <span className="text-slate-300">·</span>

          <span className="inline-flex items-center gap-1 font-mono text-slate-600">
            <Phone className="h-3 w-3 text-slate-400" />
            {doc.user_phone}
          </span>

          <span className="text-slate-300">·</span>

          <span className="rounded-md border border-slate-200/70 bg-slate-100/80 px-2 py-0.5 font-mono text-[11px] font-medium text-slate-600">
            {doc.case_no}
          </span>
        </div>
      </div>

      {/* 底部操作与链接 */}
      <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
        <div className="flex items-center gap-1 text-xs font-medium text-slate-400 transition-colors group-hover:text-cyan-700">
          <Eye className="h-3.5 w-3.5" />
          <span>查看详情</span>
        </div>

        <div className="flex items-center gap-2">
          {isPending && onDiscard && (
            <button
              type="button"
              onClick={handleDiscard}
              disabled={discarding}
              aria-busy={discarding || undefined}
              className="inline-flex min-h-8 items-center justify-center rounded-lg border border-rose-200 bg-rose-50/60 px-3 text-xs font-bold text-rose-700 transition-all hover:border-rose-300 hover:bg-rose-100 hover:text-rose-800 active:scale-95 disabled:opacity-50"
            >
              {discarding && <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
              {discarding ? "处理中…" : "不发送"}
            </button>
          )}

          <ReviewCallToAction isPending={isPending} />
        </div>
      </div>
    </Link>
  );
}

/**
 * 卡片主操作。必须渲染在 Link 内部：useLinkStatus 取的是所属 Link 的导航挂起状态，
 * 让运营按下的那一刻就看到"正在打开…"，而不是盯着毫无变化的旧页面反复点击。
 */
function ReviewCallToAction({ isPending }: { isPending: boolean }) {
  const { pending } = useLinkStatus();
  const label = pending ? "正在打开…" : isPending ? "编辑并审核" : "继续并审核";

  return (
    <div
      aria-live="polite"
      className={`inline-flex min-h-8 items-center justify-center gap-1 rounded-lg px-3 text-xs font-bold transition-all ${
        isPending
          ? "bg-cyan-700 text-white shadow-xs group-hover:bg-cyan-600"
          : "border border-slate-200 bg-slate-50 text-slate-700 group-hover:border-cyan-300 group-hover:bg-cyan-50 group-hover:text-cyan-800"
      } ${pending ? "opacity-90" : ""}`}
    >
      <span>{label}</span>
      {pending ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <ArrowRight className="h-3.5 w-3.5" />
      )}
    </div>
  );
}
