import Link from "next/link";
import { api, User, ServiceCase } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";
import { DeleteServiceCaseButton } from "./user-actions";

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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">
            {showDeleted ? "已删除用户" : "用户管理"}
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            {showDeleted
              ? "查看已删除用户的历史记录"
              : filterLabel
                ? `当前筛选：${filterLabel}`
                : "一个客户空间对应一个服务单"}
          </p>
        </div>
        <div className="flex rounded-lg border border-zinc-200 bg-white p-1 text-sm shadow-sm">
          <Link
            className={`rounded-md px-4 py-2 transition-colors ${
              !showDeleted ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-50"
            }`}
            href="/users"
          >
            正常用户
          </Link>
          <Link
            className={`rounded-md px-4 py-2 transition-colors ${
              showDeleted ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-50"
            }`}
            href="/users?deleted=1"
          >
            已删除
          </Link>
        </div>
      </div>

      {filterLabel && (
        <div className="flex items-center justify-between rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm">
          <span className="text-blue-800">已按“{filterLabel}”筛选服务单。</span>
          <Link href="/users" className="font-medium text-blue-700 hover:underline">
            清除筛选
          </Link>
        </div>
      )}

      <div className="grid gap-6">
        {showDeleted
          ? users.map((user) => <DeletedUserCard key={user.id} user={user} />)
          : serviceCases.map((serviceCase) => <ServiceCaseCard key={serviceCase.id} serviceCase={serviceCase} />)}
        {(showDeleted ? users.length : serviceCases.length) === 0 && (
          <div className="rounded-xl border border-zinc-200 bg-white p-12 text-center text-zinc-500">
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
    <div className="rounded-xl border border-zinc-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between border-b border-zinc-100 p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-100 text-xl font-bold text-blue-700">
            {(user?.name ?? "用").charAt(0)}
          </div>
          <div>
            <h3 className="text-lg font-semibold text-zinc-900">
              {user ? (
                <Link href={`/users/${user.id}`} className="hover:text-blue-600 hover:underline">
                  {user.name}
                </Link>
              ) : (
                "待填写问卷用户"
              )}
            </h3>
            <div className="mt-1 flex items-center gap-3 text-sm text-zinc-500">
              {user?.phone && <span>📱 {user.phone}</span>}
              {user?.wecom_user_id && <span>💬 已绑定企微</span>}
            </div>
          </div>
        </div>
        {user && <DeleteServiceCaseButton serviceCaseId={serviceCase.id} userName={user.name} />}
      </div>

      <div className="p-6">
        <h4 className="mb-4 text-sm font-medium text-zinc-500">服务单</h4>
        <ServiceCaseItem serviceCase={serviceCase} />
      </div>
    </div>
  );
}

function DeletedUserCard({ user }: { user: User }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="flex items-start justify-between border-b border-zinc-100 p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-100 text-xl font-bold text-zinc-600">
            {user.name.charAt(0)}
          </div>
          <div>
            <h3 className="text-lg font-semibold text-zinc-900">{user.name}</h3>
            <div className="mt-1 flex items-center gap-3 text-sm text-zinc-500">
              {user.phone && <span>📱 {user.phone}</span>}
              {user.wecom_user_id && <span>💬 已绑定企微</span>}
            </div>
          </div>
        </div>
        <span className="text-sm text-zinc-400">删除于 {formatChinaShortDateTime(user.deleted_at)}</span>
      </div>
      <div className="p-6">
        <h4 className="mb-4 text-sm font-medium text-zinc-500">历史服务单</h4>
        {user.service_cases?.length ? (
          <div className="space-y-3">
            {user.service_cases.map((serviceCase) => <ServiceCaseItem key={serviceCase.id} serviceCase={serviceCase} />)}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">暂无服务单</p>
        )}
      </div>
    </div>
  );
}

function ServiceCaseItem({ serviceCase }: { serviceCase: ServiceCase }) {
  const statusColors: Record<string, string> = {
    created: "bg-zinc-100 text-zinc-600",
    questionnaire_submitted: "bg-yellow-50 text-yellow-700",
    documents_generated: "bg-blue-50 text-blue-700",
    reviewing: "bg-purple-50 text-purple-700",
    approved: "bg-green-50 text-green-700",
    in_service: "bg-emerald-50 text-emerald-700",
    completed: "bg-gray-50 text-gray-600",
  };

  const statusLabels: Record<string, string> = {
    created: "已创建",
    questionnaire_submitted: "已提交问卷",
    documents_generated: "已生成文档",
    reviewing: "审核中",
    approved: "已批准",
    in_service: "服务中",
    completed: "已完成",
  };

  return (
    <Link
      href={`/cases/${serviceCase.id}`}
      className="flex items-center justify-between rounded-lg border border-zinc-200 p-4 transition-colors hover:border-blue-300 hover:bg-blue-50/30"
    >
      <div className="flex items-center gap-4">
        <div className="text-sm font-medium text-zinc-900">{serviceCase.case_no}</div>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${statusColors[serviceCase.status] || "bg-zinc-100 text-zinc-600"}`}>
          {statusLabels[serviceCase.status] || serviceCase.status}
        </span>
        {serviceCase.needs_human_intervention && (
          <span className="rounded-full bg-red-50 px-3 py-1 text-xs font-medium text-red-700">待人工介入</span>
        )}
        {serviceCase.risk_level === "high" && (
          <span className="rounded-full bg-orange-50 px-3 py-1 text-xs font-medium text-orange-700">高风险</span>
        )}
      </div>
      <div className="flex items-center gap-4 text-sm text-zinc-500">
        <span>群: {serviceCase.group_id.slice(0, 20)}...</span>
        <span>{formatChinaShortDateTime(serviceCase.created_at)}</span>
        <span className="text-blue-600">查看详情 →</span>
      </div>
    </Link>
  );
}
