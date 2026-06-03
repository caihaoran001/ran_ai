"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { api, UserDocument } from "@/lib/api";
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
        return doc.status === "approved" || doc.status === "sent";
      }
      return true;
    });
  }, [documents, searchQuery, statusFilter]);

  const pendingCount = useMemo(() => {
    return documents.filter((doc) => doc.status === "reviewing" || doc.status === "draft").length;
  }, [documents]);

  const reviewedCount = useMemo(() => {
    return documents.filter((doc) => doc.status === "approved" || doc.status === "sent").length;
  }, [documents]);

  if (loading) {
    return (
      <div className="space-y-6">
        {/* Search/Filter Skeleton */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between bg-white p-4.5 rounded-2xl border border-zinc-200 shadow-xs animate-pulse">
          <div className="h-10 w-full sm:w-80 bg-zinc-100 rounded-xl" />
          <div className="flex gap-2 h-10 w-64 bg-zinc-100 rounded-xl" />
        </div>

        {/* Grid Skeleton */}
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-4 bg-white p-5 rounded-2xl border border-zinc-200 shadow-xs">
            <div className="h-6 w-32 bg-zinc-100 rounded animate-pulse" />
            <div className="h-32 bg-zinc-50 border border-zinc-200 rounded-xl animate-pulse" />
            <div className="h-32 bg-zinc-50 border border-zinc-200 rounded-xl animate-pulse" />
          </div>
          <div className="space-y-4 bg-white p-5 rounded-2xl border border-zinc-200 shadow-xs">
            <div className="h-6 w-32 bg-zinc-100 rounded animate-pulse" />
            <div className="h-32 bg-zinc-50 border border-zinc-200 rounded-xl animate-pulse" />
            <div className="h-32 bg-zinc-50 border border-zinc-200 rounded-xl animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  const pendingDocs = filteredDocuments.filter((doc) => doc.status === "reviewing" || doc.status === "draft");
  const reviewedDocs = filteredDocuments.filter((doc) => doc.status === "approved" || doc.status === "sent");

  return (
    <div className="space-y-6">
      {/* Search and Filters bar */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between bg-white p-4.5 rounded-2xl border border-zinc-200 shadow-xs">
        {/* Interactive Search Box */}
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            type="text"
            placeholder="搜索文档名称、客户姓名、电话、服务单号..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-zinc-50/50 hover:bg-zinc-50 focus:bg-white border border-zinc-200 hover:border-zinc-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 text-zinc-800 placeholder-zinc-400 pl-10 pr-4 py-2.5 rounded-xl text-sm transition-all focus:outline-none shadow-2xs"
          />
        </div>

        {/* Filter buttons and Refresh button */}
        <div className="flex items-center gap-3">
          <div className="flex p-1 bg-zinc-100/80 border border-zinc-200/50 rounded-xl shadow-2xs">
            <button
              onClick={() => setStatusFilter("all")}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                statusFilter === "all"
                  ? "bg-white text-zinc-800 shadow-xs border border-zinc-200/20"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              全部 ({documents.length})
            </button>
            <button
              onClick={() => setStatusFilter("pending")}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                statusFilter === "pending"
                  ? "bg-white text-amber-600 shadow-xs border border-zinc-200/20"
                  : "text-zinc-500 hover:text-amber-600/90"
              }`}
            >
              待审核 ({pendingCount})
            </button>
            <button
              onClick={() => setStatusFilter("reviewed")}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                statusFilter === "reviewed"
                  ? "bg-white text-emerald-600 shadow-xs border border-zinc-200/20"
                  : "text-zinc-500 hover:text-emerald-600/90"
              }`}
            >
              已审核 ({reviewedCount})
            </button>
          </div>

          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="p-2.5 bg-white border border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/50 text-zinc-500 hover:text-zinc-800 rounded-xl transition-all focus:outline-none active:scale-95 shadow-2xs disabled:opacity-50"
            title="刷新文档"
          >
            <RefreshCw className={`h-4 w-4 text-zinc-500 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {documents.length === 0 ? (
        <div className="rounded-2xl border border-zinc-200 bg-white p-16 text-center shadow-xs">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-zinc-50 border border-zinc-150 shadow-inner">
            <FileText className="h-7 w-7 text-zinc-400" />
          </div>
          <p className="text-base font-bold text-zinc-800">暂无交付文件</p>
          <p className="mt-2 text-xs text-zinc-450 max-w-sm mx-auto leading-relaxed">
            用户提交入营问卷后，系统将使用大语言模型（LLM）基于专业健康评估引擎自动生成。
          </p>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Column 1: Pending */}
          {(statusFilter === "all" || statusFilter === "pending") && (
            <section className="rounded-2xl border border-zinc-200 bg-white shadow-xs overflow-hidden flex flex-col border-t-4 border-t-amber-500/80 transition-all hover:shadow-sm">
              <div className="border-b border-zinc-100 px-6 py-5 bg-gradient-to-b from-amber-50/10 to-transparent flex items-center justify-between">
                <div>
                  <h2 className="font-bold text-zinc-800 text-sm tracking-wide flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                    文件待审核
                  </h2>
                  <p className="mt-1 text-2xs text-zinc-500">新生成、需要教练二次复核并确认发送的交付件</p>
                </div>
                <span className="text-2xs font-bold bg-amber-50 text-amber-700 px-2.5 py-0.5 rounded-full border border-amber-200/60 shadow-2xs">
                  {pendingDocs.length} 份
                </span>
              </div>
              <div className="p-5 flex-1 flex flex-col bg-zinc-50/20">
                {pendingDocs.length > 0 ? (
                  <div className="max-h-[500px] overflow-y-auto space-y-3.5 pr-1.5 scrollbar-thin">
                    {pendingDocs.map((doc) => (
                      <DocumentCard key={doc.id} doc={doc} statusConfig={statusConfig} />
                    ))}
                  </div>
                ) : (
                  <div className="flex-1 flex flex-col items-center justify-center py-16 text-center text-zinc-400 border border-dashed border-zinc-200 rounded-xl bg-white/50">
                    <Clock className="h-6 w-6 text-zinc-350 mb-2 animate-pulse" />
                    <p className="text-xs font-bold text-zinc-500">暂无待审核文件</p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Column 2: Reviewed */}
          {(statusFilter === "all" || statusFilter === "reviewed") && (
            <section className="rounded-2xl border border-zinc-200 bg-white shadow-xs overflow-hidden flex flex-col border-t-4 border-t-emerald-500/80 transition-all hover:shadow-sm">
              <div className="border-b border-zinc-100 px-6 py-5 bg-gradient-to-b from-emerald-50/10 to-transparent flex items-center justify-between">
                <div>
                  <h2 className="font-bold text-zinc-800 text-sm tracking-wide flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    已审核
                  </h2>
                  <p className="mt-1 text-2xs text-zinc-500">已经教练确认发送、或审批归档的交付件</p>
                </div>
                <span className="text-2xs font-bold bg-emerald-50 text-emerald-700 px-2.5 py-0.5 rounded-full border border-emerald-200/60 shadow-2xs">
                  {reviewedDocs.length} 份
                </span>
              </div>
              <div className="p-5 flex-1 flex flex-col bg-zinc-50/20">
                {reviewedDocs.length > 0 ? (
                  <div className="max-h-[500px] overflow-y-auto space-y-3.5 pr-1.5 scrollbar-thin">
                    {reviewedDocs.map((doc) => (
                      <DocumentCard key={doc.id} doc={doc} statusConfig={statusConfig} />
                    ))}
                  </div>
                ) : (
                  <div className="flex-1 flex flex-col items-center justify-center py-16 text-center text-zinc-400 border border-dashed border-zinc-200 rounded-xl bg-white/50">
                    <ShieldCheck className="h-6 w-6 text-zinc-350 mb-2" />
                    <p className="text-xs font-bold text-zinc-500">暂无已审核文件</p>
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
}: {
  doc: MappedDocument;
  statusConfig: Record<string, { label: string; className: string }>;
}) {
  const status = statusConfig[doc.status] || {
    label: doc.status,
    className: "bg-zinc-100 text-zinc-650 border-zinc-200",
  };

  const isPending = doc.status === "reviewing" || doc.status === "draft";

  return (
    <Link
      href={`/documents/${doc.id}`}
      className="group relative block rounded-xl border border-zinc-200 bg-white p-4 transition-all hover:bg-zinc-50/20 hover:border-blue-400 hover:shadow-xs hover:-translate-y-0.5"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5">
          <div className="font-bold text-zinc-800 group-hover:text-blue-600 leading-snug text-sm transition-colors">
            {doc.title}
          </div>
          <div className="text-2xs text-zinc-500 font-semibold flex flex-wrap items-center gap-1.5">
            <span className="text-zinc-600 font-bold">{doc.user_name}</span>
            <span className="text-zinc-300">•</span>
            <span className="bg-zinc-50 text-zinc-600 px-1.5 py-0.5 rounded border border-zinc-200/60 font-medium">
              {doc.user_phone}
            </span>
            <span className="text-zinc-300">•</span>
            <span className="text-zinc-500 font-mono text-3xs tracking-wider">{doc.case_no}</span>
          </div>
        </div>
        <span
          className={`shrink-0 rounded-md border px-2 py-0.5 text-3xs font-bold tracking-wide uppercase shadow-2xs ${status.className}`}
        >
          {status.label}
        </span>
      </div>
      
      <div className="mt-3.5 pt-3 border-t border-zinc-100 flex items-center justify-between">
        <div className="text-3xs text-zinc-400 group-hover:text-zinc-500 flex items-center gap-1 transition-colors">
          <Eye className="h-3 w-3" />
          <span>查看详情</span>
        </div>
        <div className="text-2xs font-bold text-blue-600 group-hover:text-blue-500 flex items-center gap-0.5 transition-colors">
          {isPending ? "编辑并审核" : "继续并审核"} 
          <ArrowRight className="h-3.5 w-3.5 group-hover:translate-x-0.5 transition-transform" />
        </div>
      </div>
    </Link>
  );
}
