"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link, { useLinkStatus } from "next/link";
import { api, ServiceCase, User } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";
import { DeleteServiceCaseButton } from "./user-actions";
import { ArrowRight, Clock, LoaderCircle, MessageSquare, Phone } from "lucide-react";
import { GroupTypeBadge } from "@/components/group-type-badge";
import { SERVICE_CASE_PAGE_SIZE } from "./list-constants";

/** 返回用户管理时用来还原滚动位置。 */
const USERS_SCROLL_KEY = "users-list-scroll";

export function ServiceCaseList({
  initialCases,
  total,
  filter,
  query,
  groupType,
}: {
  initialCases: ServiceCase[];
  total: number;
  filter?: string;
  query?: string;
  groupType?: string;
}) {
  const [cases, setCases] = useState(initialCases);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const restored = useRef(false);

  // 切换筛选/搜索后服务端会传来新的第一页，重置本地累积结果。
  useEffect(() => {
    setCases(initialCases);
    setError(null);
  }, [initialCases]);

  const rememberScroll = useCallback(() => {
    try {
      sessionStorage.setItem(
        USERS_SCROLL_KEY,
        // loaded 一起记：翻过几页才走的，返回时得先把这些条目补回来，
        // 否则页面变短、滚动位置被浏览器截断，人还是落不到原来那张卡片上。
        JSON.stringify({ page: window.scrollY, loaded: cases.length, at: Date.now() }),
      );
    } catch {
      // 隐私模式禁用 sessionStorage 时放弃记录，不影响跳转
    }
  }, [cases.length]);

  // 从用户详情返回时回到刚才那张卡片；从侧边栏正常进入不还原（进入即消耗掉标记）。
  useEffect(() => {
    if (restored.current || cases.length === 0) return;
    let raw: string | null = null;
    try {
      raw = sessionStorage.getItem(USERS_SCROLL_KEY);
      if (raw) sessionStorage.removeItem(USERS_SCROLL_KEY);
    } catch {
      return;
    }
    restored.current = true;
    if (!raw) return;
    let saved: { page?: number; loaded?: number; at?: number };
    try {
      saved = JSON.parse(raw);
    } catch {
      return;
    }
    if (!saved.page || !saved.at || Date.now() - saved.at > 10 * 60 * 1000) return;

    // 双帧：等路由自己的滚动处理跑完再定位，否则会被重置回顶部。
    const scrollBack = () =>
      requestAnimationFrame(() =>
        requestAnimationFrame(() => window.scrollTo({ top: saved.page!, behavior: "auto" })),
      );

    const missing = Math.min((saved.loaded ?? 0) - cases.length, 500);
    if (missing <= 0) {
      scrollBack();
      return;
    }

    // 之前翻过页：先把那些条目补回来，页面恢复原来的长度后再定位。
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams();
    if (filter) params.set("filter", filter);
    if (query) params.set("q", query);
    if (groupType) params.set("group_type", groupType);
    params.set("limit", String(missing));
    params.set("offset", String(cases.length));
    api<ServiceCase[]>(`/service-cases?${params.toString()}`)
      .then((page) => {
        if (cancelled) return;
        setCases((current) => [...current, ...page]);
        scrollBack();
      })
      .catch((err) => {
        console.error("restore loaded pages failed", err);
        if (!cancelled) scrollBack();
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cases.length, filter, query, groupType]);

  const loadMore = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (filter) params.set("filter", filter);
      if (query) params.set("q", query);
      if (groupType) params.set("group_type", groupType);
      params.set("limit", String(SERVICE_CASE_PAGE_SIZE));
      params.set("offset", String(cases.length));
      const page = await api<ServiceCase[]>(`/service-cases?${params.toString()}`);
      setCases((current) => [...current, ...page]);
    } catch (err) {
      console.error("load more service cases failed", err);
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [cases.length, filter, query, groupType]);

  const hasMore = cases.length < total;

  return (
    <div className="grid gap-6">
      {cases.map((serviceCase) => (
        <ServiceCaseCard key={serviceCase.id} serviceCase={serviceCase} onOpen={rememberScroll} />
      ))}

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700"
        >
          加载更多失败：{error}
        </p>
      )}

      {hasMore && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loading}
          aria-busy={loading || undefined}
          className="inline-flex min-h-11 w-full items-center justify-center gap-1.5 rounded-lg border border-cyan-200 bg-white px-4 text-sm font-bold text-cyan-800 shadow-xs transition-all hover:border-cyan-300 hover:bg-cyan-50 active:scale-[0.99] disabled:opacity-50"
        >
          {loading && (
            <LoaderCircle
              className="h-4 w-4 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          )}
          {loading
            ? "正在加载…"
            : `加载更多（已显示 ${cases.length} / ${total}）`}
        </button>
      )}
    </div>
  );
}

export function ServiceCaseCard({
  serviceCase,
  onOpen,
}: {
  serviceCase: ServiceCase;
  /** 进详情前记下滚动位置，返回时才能回到这张卡片而不是页面顶部。 */
  onOpen?: () => void;
}) {
  const user = serviceCase.user;
  return (
    <div className="panel transition-colors duration-200 hover:border-cyan-200">
      <div className="panel-header-clean flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-cyan-200 bg-cyan-50 text-lg font-black text-cyan-900 sm:h-12 sm:w-12 sm:text-xl">
            {(user?.name ?? "用").charAt(0)}
          </div>
          <div className="min-w-0">
            <h3 className="text-lg font-bold text-slate-950">
              {user ? (
                <Link
                  href={`/users/${user.id}`}
                  prefetch={false}
                  onClick={onOpen}
                  className="inline-flex items-center gap-1.5 text-break transition-colors hover:text-cyan-700"
                >
                  {user.name}
                  <OpeningIndicator />
                </Link>
              ) : (
                "待识别用户"
              )}
            </h3>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-semibold text-slate-500">
              {user?.phone && (
                <span className="flex items-center gap-1">
                  <Phone className="h-3.5 w-3.5 text-slate-400" />
                  {user.phone}
                </span>
              )}
              {user?.wecom_user_id && (
                <span className="flex items-center gap-1 rounded-full border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-emerald-700">
                  <MessageSquare className="h-3.5 w-3.5 text-emerald-500" />
                  已绑定聊天用户
                </span>
              )}
            </div>
          </div>
        </div>
        {user && (
          <div className="sm:shrink-0">
            <DeleteServiceCaseButton serviceCaseId={serviceCase.id} userName={user.name} />
          </div>
        )}
      </div>

      <div className="panel-body">
        <h4 className="section-label mb-3">服务单</h4>
        <ServiceCaseItem serviceCase={serviceCase} onOpen={onOpen} />
      </div>
    </div>
  );
}

export function DeletedUserCard({ user }: { user: User }) {
  return (
    <div className="panel transition-colors duration-200 hover:border-slate-300">
      <div className="panel-header-clean flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-lg font-bold text-slate-500 sm:h-12 sm:w-12 sm:text-xl">
            {user.name.charAt(0)}
          </div>
          <div className="min-w-0">
            <h3 className="text-break text-lg font-bold text-slate-950">{user.name}</h3>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-semibold text-slate-500">
              {user.phone && (
                <span className="flex items-center gap-1">
                  <Phone className="h-3.5 w-3.5 text-slate-400" />
                  {user.phone}
                </span>
              )}
              {user.wecom_user_id && (
                <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-slate-500">
                  <MessageSquare className="h-3.5 w-3.5 text-slate-400" />
                  已绑定聊天用户
                </span>
              )}
            </div>
          </div>
        </div>
        <span className="flex items-center gap-1 text-xs font-semibold text-slate-400">
          <Clock className="h-3.5 w-3.5" />
          删除于 {formatChinaShortDateTime(user.deleted_at)}
        </span>
      </div>
      <div className="panel-body">
        <h4 className="section-label mb-3">历史服务单</h4>
        {user.service_cases?.length ? (
          <div className="space-y-3">
            {user.service_cases.map((serviceCase) => (
              <ServiceCaseItem key={serviceCase.id} serviceCase={serviceCase} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">暂无服务单</p>
        )}
      </div>
    </div>
  );
}


function ServiceCaseItem({
  serviceCase,
  onOpen,
}: {
  serviceCase: ServiceCase;
  onOpen?: () => void;
}) {
  const continued = serviceCase.status === "in_service" && Boolean(serviceCase.service_continued_at);
  const statusColors: Record<string, string> = {
    created: "border-slate-200 bg-slate-50 text-slate-600",
    questionnaire_submitted: "border-amber-200 bg-amber-50 text-amber-700",
    generating: "border-cyan-200 bg-cyan-50 text-cyan-700",
    documents_generated: "border-cyan-200 bg-cyan-50 text-cyan-700",
    reviewing: "border-violet-200 bg-violet-50 text-violet-700",
    approved: "border-emerald-200 bg-emerald-50 text-emerald-700",
    in_service: "border-emerald-200 bg-emerald-50 text-emerald-700",
    completed: "border-slate-200 bg-slate-50 text-slate-600",
    refunded: "border-rose-200 bg-rose-50 text-rose-700",
  };

  const statusLabels: Record<string, string> = {
    created: "已创建",
    questionnaire_submitted: "已提交问卷",
    generating: "文件生成中",
    documents_generated: "已生成文档",
    reviewing: "审核中",
    approved: "已批准",
    in_service: "服务中",
    completed: "阶段已完结",
    refunded: "已退款",
  };

  return (
    <Link
      href={`/cases/${serviceCase.id}`}
      prefetch={false}
      onClick={onOpen}
      className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4 transition-colors hover:border-cyan-300 hover:bg-white sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <div className="text-break text-sm font-semibold text-slate-950">{serviceCase.case_no}</div>
        <GroupTypeBadge groupType={serviceCase.group_type} />
        <span
          className={`status-pill ${
            continued
              ? "border-cyan-200 bg-cyan-50 text-cyan-800"
              : statusColors[serviceCase.status] || "border-slate-200 bg-slate-50 text-slate-600"
          }`}
        >
          <span className="inline-flex items-center gap-1">
            {serviceCase.status === "generating" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
            {continued ? "续服中" : statusLabels[serviceCase.status] || serviceCase.status}
          </span>
        </span>
        {serviceCase.risk_level === "high" && (
          <span className="status-pill border-orange-200 bg-orange-50 text-orange-700">高风险</span>
        )}
        {serviceCase.plateau_status === "plateau" && (
          <span
            className="status-pill border-violet-200 bg-violet-50 text-violet-700"
            title={plateauBasisTitle(serviceCase.plateau_evidence)}
          >
            平台期
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-500 sm:shrink-0 sm:justify-end">
        <span className="hidden max-w-52 truncate lg:inline">
          群: {serviceCase.group_name || serviceCase.group_id}
        </span>
        <span>{formatChinaShortDateTime(serviceCase.created_at)}</span>
        <ViewDetailCta />
      </div>
    </Link>
  );
}

/** 服务单行的"查看详情"，按下即转圈——列表页不换画面时运营会以为没点上。 */
function ViewDetailCta() {
  const { pending } = useLinkStatus();
  return (
    <span className="inline-flex items-center gap-1 font-semibold text-cyan-700" aria-live="polite">
      {pending ? "正在打开…" : "查看详情"}
      {pending ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <ArrowRight className="h-3.5 w-3.5" />
      )}
    </span>
  );
}

/** 用户名链接的挂起提示：只在跳转进行中出现，平时不占位。 */
function OpeningIndicator() {
  const { pending } = useLinkStatus();
  if (!pending) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-cyan-700">
      <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      正在打开…
    </span>
  );
}

function plateauBasisTitle(evidence?: Record<string, unknown> | null): string {
  const basis = Array.isArray(evidence?.basis) ? evidence.basis : [];
  if (basis.length > 0) {
    return `依据：${basis
      .map((item) => (item === "statistical" ? "近7天体重无变化" : "用户自述体重卡住"))
      .join("、")}`;
  }
  return "近 7 天体重无变化或用户自述体重卡住";
}
