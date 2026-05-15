import Link from "next/link";
import { api, User, ServiceCase } from "@/lib/api";
import { DeleteUserButton } from "./user-actions";

export default async function UsersPage({
  searchParams,
}: {
  searchParams: Promise<{ deleted?: string }>;
}) {
  const { deleted } = await searchParams;
  const showDeleted = deleted === "1";
  const users = await api<User[]>(`/users${showDeleted ? "?deleted=true" : ""}`).catch(() => []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">
            {showDeleted ? "已删除用户" : "用户管理"}
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            {showDeleted ? "查看已删除用户的历史记录" : "管理用户及其服务空间"}
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

      <div className="grid gap-6">
        {users.map((user) => (
          <UserCard key={user.id} user={user} showDeleted={showDeleted} />
        ))}
        {users.length === 0 && (
          <div className="rounded-xl border border-zinc-200 bg-white p-12 text-center text-zinc-500">
            {showDeleted ? "暂无已删除用户" : "暂无用户"}
          </div>
        )}
      </div>
    </div>
  );
}

function UserCard({ user, showDeleted }: { user: User; showDeleted: boolean }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between border-b border-zinc-100 p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-100 text-xl font-bold text-blue-700">
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
        <div>
          {showDeleted ? (
            <span className="text-sm text-zinc-400">
              删除于 {formatDateTime(user.deleted_at)}
            </span>
          ) : (
            <DeleteUserButton userId={user.id} userName={user.name} />
          )}
        </div>
      </div>

      <div className="p-6">
        <h4 className="mb-4 text-sm font-medium text-zinc-500">服务单</h4>
        {user.service_cases && user.service_cases.length > 0 ? (
          <div className="space-y-3">
            {user.service_cases.map((serviceCase) => (
              <ServiceCaseItem key={serviceCase.id} serviceCase={serviceCase} />
            ))}
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
      </div>
      <div className="flex items-center gap-4 text-sm text-zinc-500">
        <span>群: {serviceCase.group_id.slice(0, 20)}...</span>
        <span>{formatDateTime(serviceCase.created_at)}</span>
        <span className="text-blue-600">查看详情 →</span>
      </div>
    </Link>
  );
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
