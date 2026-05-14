import Link from "next/link";
import { api, User } from "@/lib/api";

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await api<User>(`/users/${id}`);
  const profile = user.latest_profile;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{user.name}</h1>
        <p className="mt-1 text-sm text-zinc-500">{user.phone ?? "无手机号"} · {user.wecom_user_id ?? "无企微用户 ID"}</p>
        {user.deleted_at && (
          <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
            已删除：{formatDateTime(user.deleted_at)}
          </p>
        )}
      </div>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">用户画像</h2>
        {profile ? (
          <ProfileView profile={profile.profile_json} fallback={profile.summary} />
        ) : (
          <p className="mt-3 text-sm text-zinc-500">尚未生成用户画像。用户提交问卷后会自动生成一次，也可以在服务单详情手动触发。</p>
        )}
      </section>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">服务单</h2>
        <div className="mt-3 grid gap-2 text-sm">
          {user.service_cases.map((item) => (
            <Link key={item.id} className="rounded-md border border-zinc-200 px-3 py-2 hover:bg-zinc-50" href={`/cases/${item.id}`}>
              {item.case_no} · {item.group_id} · {item.status}
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

function ProfileView({ profile, fallback }: { profile: Record<string, unknown>; fallback: string }) {
  const summary = text(profile.summary) || fallback;
  const items = [
    ["当前状态", text(profile.state)],
    ["核心问题", text(profile.main_problem)],
    ["28 天目标", text(profile.day28_goal)],
    ["风险提醒", text(profile.risks)],
    ["运营备注", text(profile.notes)],
  ].filter(([, value]) => value);

  return (
    <div className="mt-3 space-y-4 text-sm leading-6">
      <p>{summary}</p>
      <div className="grid gap-3 md:grid-cols-2">
        {items.map(([label, value]) => (
          <div key={label} className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
            <div className="text-xs font-medium text-zinc-500">{label}</div>
            <div className="mt-1">{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
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
