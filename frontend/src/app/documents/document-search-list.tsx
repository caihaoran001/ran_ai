"use client";

import { useState, useEffect, useMemo, useRef, type MouseEvent } from "react";
import Link from "next/link";
import { api, UserDocument, API_BASE } from "@/lib/api";
import { useSSE } from "@/hooks/use-sse";
import { FileText, Search, RefreshCw, Eye, CheckCircle, ArrowRight, Clock, ShieldCheck } from "lucide-react";

interface MappedDocument extends UserDocument {
  case_no: string;
  group_id: string;
  user_name: string;
  user_phone: string;
}

export function DocumentSearchList() {
  const [documents, setDocuments] = useState<MappedDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "reviewed">("all");
  const [refreshing, setRefreshing] = useState(false);
  const { pendingDocuments } = useSSE();
  const lastPendingDocuments = useRef<number | null>(null);

  const fetchDocuments = async () => {
    try {
      // 1. Fetch all documents in a single O(1) request
      const rawDocs = await api<UserDocument[]>("/documents");
      
      // 2. Fetch service cases concurrently to map user details in $O(1)$
      const cases = await api<any[]>("/service-cases");
      const casesMap = new Map(cases.map((c) => [c.id, c]));

      const mapped: MappedDocument[] = rawDocs.map((doc) => {
        const sc = casesMap.get(doc.service_case_id);
        return {
          ...doc,
          case_no: sc?.case_no || "未知服务单",
          group_id: sc?.group_id || "未知群组",
          user_name: sc?.user?.name || "未知客户",
          user_phone: sc?.user?.phone || "无手机号",
        };
      });

      setDocuments(mapped);
    } catch (err) {
      console.error("Failed to fetch documents in batch:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  useEffect(() => {
    if (lastPendingDocuments.current === null) {
      lastPendingDocuments.current = pendingDocuments;
      return;
    }
    if (lastPendingDocuments.current !== pendingDocuments) {
      lastPendingDocuments.current = pendingDocuments;
      fetchDocuments();
    }
  }, [pendingDocuments]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchDocuments();
  };

  const statusConfig: Record<string, { label: string; className: string }> = {
    reviewing: { label: "待审核", className: "bg-amber-50 text-amber-700 border-amber-200/80" },
    approved: { label: "已批准", className: "bg-emerald-50 text-emerald-700 border-emerald-200/80" },
    sent: { label: "已发送", className: "bg-blue-50 text-blue-700 border-blue-200/80" },
    draft: { label: "草稿", className: "bg-zinc-100 text-zinc-600 border-zinc-200" },
    archived: { label: "未发送", className: "bg-zinc-100 text-zinc-500 border-zinc-300" },
  };

  // Client-side fuzzy search and status filter
  const filteredDocuments = useMemo(() => {
    return documents.filter((doc) => {
      // Search filter
      const query = searchQuery.trim().toLowerCase();
      const matchesSearch =
        !query ||
        doc.title.toLowerCase().includes(query) ||
        doc.user_name.toLowerCase().includes(query) ||
        doc.user_phone.toLowerCase().includes(query) ||
        doc.case_no.toLowerCase().includes(query);

      // Status filter
      if (!matchesSearch) return false;
      if (statusFilter === "pending") {
        return doc.status === "reviewing" || doc.status === "draft";
      }
      if (statusFilter === "reviewed") {
        return doc.status === "approved" || doc.status === "sent" || doc.status === "archived";
      }
      return true;
    });
  }, [documents, searchQuery, statusFilter]);

  const pendingCount = useMemo(() => {
    return documents.filter((doc) => doc.status === "reviewing" || doc.status === "draft").length;
  }, [documents]);

  const reviewedCount = useMemo(() => {
    return documents.filter((doc) => doc.status === "approved" || doc.status === "sent" || doc.status === "archived").length;
  }, [documents]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="flex animate-pulse flex-col gap-4 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="h-10 w-full rounded-lg bg-slate-100 sm:w-80" />
          <div className="h-10 w-full rounded-lg bg-slate-100 sm:w-64" />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <div className="h-6 w-32 animate-pulse rounded bg-slate-100" />
            <div className="h-32 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
            <div className="h-32 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
          </div>
          <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <div className="h-6 w-32 animate-pulse rounded bg-slate-100" />
            <div className="h-32 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
            <div className="h-32 animate-pulse rounded-xl border border-slate-200 bg-slate-50" />
          </div>
        </div>
      </div>
    );
  }

  const pendingDocs = filteredDocuments.filter((doc) => doc.status === "reviewing" || doc.status === "draft");
  const reviewedDocs = filteredDocuments.filter((doc) => doc.status === "approved" || doc.status === "sent" || doc.status === "archived");

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative min-w-0 flex-1 sm:max-w-md">
          <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="搜索文档名称、客户姓名、电话、服务单号..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-11 w-full rounded-lg border border-slate-200 bg-slate-50/60 pl-10 pr-4 text-sm text-slate-900 transition-colors placeholder:text-slate-500 hover:bg-slate-50 focus:border-cyan-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-cyan-100"
          />
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="grid grid-cols-3 rounded-lg border border-slate-200 bg-slate-100 p-1 text-center">
            <button
              onClick={() => setStatusFilter("all")}
              className={`min-h-10 rounded-md px-2 py-2 text-xs font-bold transition-colors sm:px-4 ${
                statusFilter === "all"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              全部 ({documents.length})
            </button>
            <button
              onClick={() => setStatusFilter("pending")}
              className={`min-h-10 rounded-md px-2 py-2 text-xs font-bold transition-colors sm:px-4 ${
                statusFilter === "pending"
                  ? "bg-white text-amber-700 shadow-sm"
                  : "text-slate-600 hover:text-amber-700"
              }`}
            >
              待审核 ({pendingCount})
            </button>
            <button
              onClick={() => setStatusFilter("reviewed")}
              className={`min-h-10 rounded-md px-2 py-2 text-xs font-bold transition-colors sm:px-4 ${
                statusFilter === "reviewed"
                  ? "bg-white text-emerald-700 shadow-sm"
                  : "text-slate-600 hover:text-emerald-700"
              }`}
            >
              已审核 ({reviewedCount})
            </button>
          </div>

          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-slate-600 transition-colors hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-800 disabled:opacity-50"
            title="刷新文档"
          >
            <RefreshCw className={`h-4 w-4 text-zinc-500 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {documents.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center sm:p-12">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-slate-200 bg-slate-50">
            <FileText className="h-6 w-6 text-slate-400" />
          </div>
          <p className="text-base font-bold text-slate-800">暂无交付文件</p>
          <p className="mx-auto mt-2 max-w-sm text-xs leading-relaxed text-slate-500">
            用户提交入营问卷后，系统将使用大语言模型（LLM）基于专业健康评估引擎自动生成。
          </p>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Column 1: Pending */}
          {(statusFilter === "all" || statusFilter === "pending") && (
            <section className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-4 sm:px-5">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800">
                    <span className="h-2 w-2 rounded-full bg-amber-500" />
                    文件待审核
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">新生成、需要教练二次复核并确认发送的交付件</p>
                </div>
                <span className="status-pill border-amber-200 bg-amber-50 text-amber-700">
                  {pendingDocs.length} 份
                </span>
              </div>
              <div className="flex flex-1 flex-col bg-slate-50/40 p-4 sm:p-5">
                {pendingDocs.length > 0 ? (
                  <div className="max-h-[500px] space-y-3.5 overflow-y-auto pr-1.5">
                    {pendingDocs.map((doc) => (
                      <DocumentCard key={doc.id} doc={doc} statusConfig={statusConfig} onDiscard={fetchDocuments} />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white/70 py-16 text-center text-slate-400">
                    <Clock className="mb-2 h-6 w-6" />
                    <p className="text-xs font-bold text-slate-500">暂无待审核文件</p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Column 2: Reviewed */}
          {(statusFilter === "all" || statusFilter === "reviewed") && (
            <section className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-4 sm:px-5">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-bold text-slate-800">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    已审核
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">已经教练确认发送、或审批归档的交付件</p>
                </div>
                <span className="status-pill border-emerald-200 bg-emerald-50 text-emerald-700">
                  {reviewedDocs.length} 份
                </span>
              </div>
              <div className="flex flex-1 flex-col bg-slate-50/40 p-4 sm:p-5">
                {reviewedDocs.length > 0 ? (
                  <div className="max-h-[500px] space-y-3.5 overflow-y-auto pr-1.5">
                    {reviewedDocs.map((doc) => (
                      <DocumentCard key={doc.id} doc={doc} statusConfig={statusConfig} />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white/70 py-16 text-center text-slate-400">
                    <ShieldCheck className="mb-2 h-6 w-6" />
                    <p className="text-xs font-bold text-slate-500">暂无已审核文件</p>
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

function DocumentCard({
  doc,
  statusConfig,
  onDiscard,
}: {
  doc: MappedDocument;
  statusConfig: Record<string, { label: string; className: string }>;
  onDiscard?: () => void;
}) {
  const [discarding, setDiscarding] = useState(false);
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
    try {
      await fetch(`${API_BASE}/documents/${doc.id}/discard`, { method: "POST" });
      onDiscard();
    } catch (err) {
      console.error("discard document failed", err);
      setDiscarding(false);
    }
  }

  return (
    <Link
      href={`/documents/${doc.id}`}
      className="group relative block rounded-xl border border-slate-200 bg-white p-4 transition-colors hover:border-cyan-300 hover:bg-cyan-50/30"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <div className="text-break text-sm font-bold leading-snug text-slate-900 transition-colors group-hover:text-cyan-700">
            {doc.title}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-xs font-semibold text-slate-500">
            <span className="font-bold text-slate-700">{doc.user_name}</span>
            <span className="text-slate-300">·</span>
            <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-medium text-slate-600">
              {doc.user_phone}
            </span>
            <span className="text-slate-300">·</span>
            <span className="text-break font-mono text-xs text-slate-500">{doc.case_no}</span>
          </div>
        </div>
        <span
          className={`shrink-0 rounded-md border px-2 py-1 text-xs font-bold ${status.className}`}
        >
          {status.label}
        </span>
      </div>
      
      <div className="mt-3.5 flex flex-col gap-3 border-t border-slate-100 pt-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1 text-xs text-slate-500 transition-colors">
          <Eye className="h-3 w-3" />
          <span>查看详情</span>
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:justify-end">
          {isPending && onDiscard && (
            <button
              type="button"
              onClick={handleDiscard}
              disabled={discarding}
              className="min-h-9 rounded-md border border-slate-200 px-3 text-xs font-bold text-slate-500 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
            >
              {discarding ? "处理中…" : "不发送"}
            </button>
          )}
          <div className="flex min-h-9 items-center gap-0.5 text-xs font-bold text-cyan-700 transition-colors group-hover:text-cyan-600">
            {isPending ? "编辑并审核" : "继续并审核"}
            <ArrowRight className="h-3.5 w-3.5" />
          </div>
        </div>
      </div>
    </Link>
  );
}
