import Link from "next/link";
import { api, User } from "@/lib/api";
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
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{showDeleted ? "已删除用户" : "用户"}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {showDeleted ? "查看已删除用户的历史记录。" : "查看用户、服务空间和已生成画像。"}
          </p>
        </div>
        <div className="flex rounded-md border border-zinc-200 bg-white p-1 text-sm">
          <Link className={`rounded px-3 py-1.5 ${!showDeleted ? "bg-zinc-900 text-white" : "text-zinc-700"}`} href="/users">
            正常用户
          </Link>
          <Link className={`rounded px-3 py-1.5 ${showDeleted ? "bg-zinc-900 text-white" : "text-zinc-700"}`} href="/users?deleted=1">
            已删除
          </Link>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-50 text-zinc-500">
            <tr>
              <th className="px-4 py-3">姓名</th>
              <th className="px-4 py-3">手机号</th>
              <th className="px-4 py-3">企微用户</th>
              <th className="px-4 py-3">服务单</th>
              <th className="px-4 py-3">{showDeleted ? "删除时间" : "操作"}</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-t border-zinc-100">
                <td className="px-4 py-3">
                  <Link className="font-medium hover:underline" href={`/users/${user.id}`}>{user.name}</Link>
                </td>
                <td className="px-4 py-3">{user.phone ?? "-"}</td>
                <td className="px-4 py-3">{user.wecom_user_id ?? "-"}</td>
                <td className="px-4 py-3">{user.service_cases?.length ?? 0}</td>
                <td className="px-4 py-3">
                  {showDeleted ? formatDateTime(user.deleted_at) : <DeleteUserButton userId={user.id} userName={user.name} />}
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr><td className="px-4 py-8 text-center text-zinc-500" colSpan={5}>{showDeleted ? "暂无已删除用户" : "暂无用户"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
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
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
