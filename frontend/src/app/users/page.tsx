import Link from "next/link";
import { api, User, ServiceCase } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";
import { DeleteServiceCaseButton } from "./user-actions";
import { CaseSearchBox } from "./case-search-box";
import { ArrowRight, Clock, LoaderCircle, MessageSquare, Phone } from "lucide-react";

export default async function UsersPage({
  searchParams,
}: {
  searchParams: Promise<{ deleted?: string; active?: string; filter?: string; q?: string }>;
}) {
  const { deleted, active, filter, q } = await searchParams;
  const showDeleted = deleted === "1";
  // 活跃用户 = 近 24 小时内有「用户发言」(inbound) 的服务单；仅在未看已删除时生效。
  const showActive = !showDeleted && active === "1";
  // 看板深链筛选(风险/未跟进等)只在「正常用户」下生效；「活跃用户」用固定的 active_24h 筛选。
  const dashboardFilter = showDeleted || showActive ? undefined : filter;
  const serviceCaseFilter = showActive ? "active_24h" : dashboardFilter;
  const searchQuery = showDeleted ? undefined : q?.trim();
  // 用户列表仅「已删除」页需要；正常/活跃页只展示服务单列表，避免多打一次无用请求。
  const users = showDeleted ? await api<User[]>(`/users?deleted=true`).catch(() => []) : [];
  const caseParams = new URLSearchParams();
  if (serviceCaseFilter) caseParams.set("filter", serviceCaseFilter);
  if (searchQuery) caseParams.set("q", searchQuery);
  const serviceCasePath = `/service-cases${caseParams.toString() ? `?${caseParams.toString()}` : ""}`;
  const serviceCases = showDeleted ? [] : await api<ServiceCase[]>(serviceCasePath).catch(() => []);
  const filterLabel = dashboardFilter ? SERVICE_CASE_FILTER_LABELS[dashboardFilter] : undefined;

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">
              {showDeleted ? "已删除用户" : showActive ? "活跃用户" : "正常用户"}
            </h1>
            <p className="page-subtitle">
              {showDeleted
                ? "查看已删除用户的历史记录"
                : showActive
                  ? "近 24 小时内有发言的用户"
                  : filterLabel
                    ? `当前筛选：${filterLabel}`
                    : "一个客户空间对应一个服务单"}
            </p>
          </div>
          <span className="status-pill border-slate-200 bg-slate-50 text-slate-700">
            {showDeleted ? users.length : serviceCases.length} 条记录
          </span>
        </div>
        <div className="mt-4 grid w-full grid-cols-3 rounded-lg border border-slate-200 bg-slate-100 p-1 text-sm shadow-inner sm:w-fit">
          <Link
            className={`min-h-10 rounded-md px-4 py-2 text-center font-semibold transition-colors duration-200 ${
              !showDeleted && !showActive ? "bg-white text-cyan-800 shadow-sm" : "text-slate-700 hover:bg-white/70 hover:text-slate-950"
            }`}
            href="/users"
          >
            正常用户
          </Link>
          <Link
            className={`min-h-10 rounded-md px-4 py-2 text-center font-semibold transition-colors duration-200 ${
              showActive ? "bg-white text-cyan-800 shadow-sm" : "text-slate-700 hover:bg-white/70 hover:text-slate-950"
            }`}
            href="/users?active=1"
          >
            活跃用户
          </Link>
          <Link
            className={`min-h-10 rounded-md px-4 py-2 text-center font-semibold transition-colors duration-200 ${
              showDeleted ? "bg-white text-cyan-800 shadow-sm" : "text-slate-700 hover:bg-white/70 hover:text-slate-950"
            }`}
            href="/users?deleted=1"
          >
            已删除
          </Link>
        </div>
        {/* 给运营的说明：区分「正常用户(全部)」与「活跃用户(近 24h 发言)」两个视图的含义。 */}
        <p className="mt-2 text-xs leading-relaxed text-slate-500">
          「正常用户」显示<span className="font-semibold text-slate-700">全部</span>用户；「活跃用户」只显示<span className="font-semibold text-slate-700">近 24 小时内有发言</span>的用户，便于优先处理今天在聊的客户，页面加载也更快。
        </p>
      </div>

      {!showDeleted && <CaseSearchBox />}

      {filterLabel && (
        <div className="flex flex-col gap-2 rounded-lg border border-cyan-200 bg-cyan-50 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <span className="text-cyan-900">已按“{filterLabel}”筛选服务单。</span>
          <Link href="/users" className="font-semibold text-cyan-800 hover:underline">
            清除筛选
          </Link>
        </div>
      )}

      <div className="grid gap-6">
        {showDeleted
          ? users.map((user) => <DeletedUserCard key={user.id} user={user} />)
          : serviceCases.map((serviceCase) => <ServiceCaseCard key={serviceCase.id} serviceCase={serviceCase} />)}
        {(showDeleted ? users.length : serviceCases.length) === 0 && (
          <div className="empty-state">
            {showDeleted
              ? "暂无已删除用户"
              : searchQuery
                ? `未找到匹配「${searchQuery}」的服务单`
                : showActive
                  ? "近 24 小时内暂无用户发言"
                  : "暂无用户"}
          </div>
        )}
      </div>
    </div>
  );
}

const SERVICE_CASE_FILTER_LABELS: Record<string, string> = {
  new_today: "今日新增用户",
  risk: "风险用户",
  unfollowed: "未跟进（2-7天）",
  renewal_followup: "续购跟进窗口",
  renewal_intent: "明确续购意向",
  refund_intent: "退款/退出意向",
  lost: "流失用户",
  in_service: "服务中",
  messaged_today: "今日有消息",
};

function ServiceCaseCard({ serviceCase }: { serviceCase: ServiceCase }) {
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
                <Link href={`/users/${user.id}`} className="text-break transition-colors hover:text-cyan-700">
                  {user.name}
                </Link>
              ) : (
                "待填写问卷用户"
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
        <ServiceCaseItem serviceCase={serviceCase} />
      </div>
    </div>
  );
}

function DeletedUserCard({ user }: { user: User }) {
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
            {user.service_cases.map((serviceCase) => <ServiceCaseItem key={serviceCase.id} serviceCase={serviceCase} />)}
          </div>
        ) : (
          <p className="text-sm text-slate-400">暂无服务单</p>
        )}
      </div>
    </div>
  );
}

function ServiceCaseItem({ serviceCase }: { serviceCase: ServiceCase }) {
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
      className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4 transition-colors hover:border-cyan-300 hover:bg-white sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <div className="text-break text-sm font-semibold text-slate-950">{serviceCase.case_no}</div>
        <span className={`status-pill ${continued ? "border-cyan-200 bg-cyan-50 text-cyan-800" : statusColors[serviceCase.status] || "border-slate-200 bg-slate-50 text-slate-600"}`}>
          <span className="inline-flex items-center gap-1">
            {serviceCase.status === "generating" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
            {continued ? "续服中" : statusLabels[serviceCase.status] || serviceCase.status}
          </span>
        </span>
        {serviceCase.risk_level === "high" && (
          <span className="status-pill border-orange-200 bg-orange-50 text-orange-700">高风险</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-500 sm:shrink-0 sm:justify-end">
        <span className="hidden max-w-52 truncate lg:inline">群: {serviceCase.group_name || serviceCase.group_id}</span>
        <span>{formatChinaShortDateTime(serviceCase.created_at)}</span>
        <span className="inline-flex items-center gap-1 font-semibold text-cyan-700">
          查看详情 <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </Link>
  );
}
