import Link from "next/link";
import { api, User, ServiceCase } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";
import { DeleteServiceCaseButton } from "./user-actions";
import { ArrowRight, Clock, LoaderCircle, MessageSquare, Phone } from "lucide-react";

export default async function UsersPage({
  searchParams,
}: {
  searchParams: Promise<{ deleted?: string; filter?: string }>;
}) {
  const { deleted, filter } = await searchParams;
  const showDeleted = deleted === "1";
  const activeFilter = showDeleted ? undefined : filter;
  const users = await api<User[]>(`/users${showDeleted ? "?deleted=true" : ""}`).catch(() => []);
  const serviceCasePath = activeFilter
    ? `/service-cases?filter=${encodeURIComponent(activeFilter)}`
    : "/service-cases";
  const serviceCases = showDeleted ? [] : await api<ServiceCase[]>(serviceCasePath).catch(() => []);
  const filterLabel = activeFilter ? SERVICE_CASE_FILTER_LABELS[activeFilter] : undefined;

  return (
    <div className="page-stack">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-heading">
            {showDeleted ? "已删除用户" : "用户管理"}
          </h1>
          <p className="page-subtitle">
            {showDeleted
              ? "查看已删除用户的历史记录"
              : filterLabel
                ? `当前筛选：${filterLabel}`
                : "一个客户空间对应一个服务单"}
          </p>
        </div>
        <div className="flex rounded-lg border border-slate-200 bg-white p-1 text-sm shadow-sm">
          <Link
            className={`rounded-md px-4 py-2 font-semibold transition-colors duration-200 ${
              !showDeleted ? "bg-cyan-700 text-white shadow-sm" : "text-slate-600 hover:bg-slate-50"
            }`}
            href="/users"
          >
            正常用户
          </Link>
          <Link
            className={`rounded-md px-4 py-2 font-semibold transition-colors duration-200 ${
              showDeleted ? "bg-cyan-700 text-white shadow-sm" : "text-slate-600 hover:bg-slate-50"
            }`}
            href="/users?deleted=1"
          >
            已删除
          </Link>
        </div>
      </div>

      {filterLabel && (
        <div className="flex items-center justify-between rounded-lg border border-cyan-200 bg-cyan-50 px-4 py-3 text-sm">
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
          <div className="panel p-12 text-center text-slate-500">
            {showDeleted ? "暂无已删除用户" : "暂无用户"}
          </div>
        )}
      </div>
    </div>
  );
}

const SERVICE_CASE_FILTER_LABELS: Record<string, string> = {
  new_today: "今日新增用户",
  risk: "风险用户",
  human_intervention: "待人工介入",
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
      <div className="flex items-start justify-between border-b border-slate-100 p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-cyan-100 bg-cyan-50 text-xl font-bold text-cyan-800 shadow-sm">
            {(user?.name ?? "用").charAt(0)}
          </div>
          <div>
            <h3 className="text-lg font-bold text-slate-950">
              {user ? (
                <Link href={`/users/${user.id}`} className="transition-colors hover:text-cyan-700">
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
        {user && <DeleteServiceCaseButton serviceCaseId={serviceCase.id} userName={user.name} />}
      </div>

      <div className="p-6">
        <h4 className="mb-3 text-xs font-bold text-slate-400">服务单</h4>
        <ServiceCaseItem serviceCase={serviceCase} />
      </div>
    </div>
  );
}

function DeletedUserCard({ user }: { user: User }) {
  return (
    <div className="panel transition-colors duration-200 hover:border-slate-300">
      <div className="flex items-start justify-between border-b border-slate-100 p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-xl font-bold text-slate-500">
            {user.name.charAt(0)}
          </div>
          <div>
            <h3 className="text-lg font-bold text-slate-950">{user.name}</h3>
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
      <div className="p-6">
        <h4 className="mb-3 text-xs font-bold text-slate-400">历史服务单</h4>
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
  const statusColors: Record<string, string> = {
    created: "border-slate-200 bg-slate-50 text-slate-600",
    questionnaire_submitted: "border-amber-200 bg-amber-50 text-amber-700",
    generating: "border-cyan-200 bg-cyan-50 text-cyan-700",
    documents_generated: "border-cyan-200 bg-cyan-50 text-cyan-700",
    reviewing: "border-violet-200 bg-violet-50 text-violet-700",
    approved: "border-emerald-200 bg-emerald-50 text-emerald-700",
    in_service: "border-emerald-200 bg-emerald-50 text-emerald-700",
    completed: "border-slate-200 bg-slate-50 text-slate-600",
  };

  const statusLabels: Record<string, string> = {
    created: "已创建",
    questionnaire_submitted: "已提交问卷",
    generating: "文件生成中",
    documents_generated: "已生成文档",
    reviewing: "审核中",
    approved: "已批准",
    in_service: "服务中",
    completed: "已完成",
  };

  return (
    <Link
      href={`/cases/${serviceCase.id}`}
      className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 p-4 transition-colors hover:border-cyan-300 hover:bg-cyan-50/30"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <div className="text-sm font-semibold text-slate-950">{serviceCase.case_no}</div>
        <span className={`status-pill ${statusColors[serviceCase.status] || "border-slate-200 bg-slate-50 text-slate-600"}`}>
          <span className="inline-flex items-center gap-1">
            {serviceCase.status === "generating" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
            {statusLabels[serviceCase.status] || serviceCase.status}
          </span>
        </span>
        {serviceCase.needs_human_intervention && (
          <span className="status-pill border-rose-200 bg-rose-50 text-rose-700">待人工介入</span>
        )}
        {serviceCase.risk_level === "high" && (
          <span className="status-pill border-orange-200 bg-orange-50 text-orange-700">高风险</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-4 text-sm text-slate-500">
        <span className="hidden max-w-52 truncate lg:inline">群: {serviceCase.group_id}</span>
        <span>{formatChinaShortDateTime(serviceCase.created_at)}</span>
        <span className="inline-flex items-center gap-1 font-semibold text-cyan-700">
          查看详情 <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </Link>
  );
}
