import Link from "next/link";
import { api } from "@/lib/api";

type Summary = {
  service_cases_total: number;
  service_cases_in_service: number;
  checkins_today: number;
  messages_today: number;
  pending_documents: number;
  new_users_today: number;
  risk_users: number;
  human_intervention_users?: number;
  unfollowed_users: number;
  renewal_followup_users?: number;
  repurchase_soon_users: number;
  renewal_intent_users?: number;
  refund_intent_users?: number;
  lost_users: number;
  emotional_users_today?: number;
};

export default async function DashboardPage() {
  const summary = await api<Summary>("/dashboard/summary").catch(() => ({
    service_cases_total: 0,
    service_cases_in_service: 0,
    checkins_today: 0,
    messages_today: 0,
    pending_documents: 0,
    new_users_today: 0,
    risk_users: 0,
    human_intervention_users: 0,
    unfollowed_users: 0,
    renewal_followup_users: 0,
    repurchase_soon_users: 0,
    renewal_intent_users: 0,
    refund_intent_users: 0,
    lost_users: 0,
    emotional_users_today: 0,
  }));
  const renewalFollowupUsers = summary.renewal_followup_users ?? summary.repurchase_soon_users;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">数据看板</h1>
        <p className="mt-1 text-sm text-zinc-500">服务概览与关键指标</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="今日新增用户"
          value={summary.new_users_today}
          icon="➕"
          color="blue"
          href="/users?filter=new_today"
          description="今天新创建且未删除的服务单。"
        />
        <StatCard
          label="风险用户"
          value={summary.risk_users}
          icon="⚠️"
          color="red"
          href="/users?filter=risk"
          description="服务单命中过孕哺期、慢病、胸痛心悸、严重失眠、医疗承诺等风险词，需要人工关注。"
        />
        <StatCard
          label="待人工介入"
          value={summary.human_intervention_users ?? 0}
          icon="🚨"
          color="red"
          href="/users?filter=human_intervention"
          description="LLM 识别到健康红线、客诉退款、医疗边界、强续购等需要真人处理的服务单。"
        />
        <StatCard
          label="未跟进（2-7天）"
          value={summary.unfollowed_users}
          icon="⏳"
          color="yellow"
          href="/users?filter=unfollowed"
          description="服务中，且最近 2-7 天没有用户入站消息的服务单；与流失用户互斥。"
        />
        <StatCard
          label="续购跟进窗口"
          value={renewalFollowupUsers}
          icon="🛒"
          color="green"
          href="/users?filter=renewal_followup"
          description="按服务天数推算：已服务 18-28 天，适合人工做续购沟通，不代表用户已表达购买意愿。"
        />
        <StatCard
          label="明确续购意向"
          value={summary.renewal_intent_users ?? 0}
          icon="💚"
          color="green"
          href="/users?filter=renewal_intent"
          description="用户聊天中出现续费、复购、再买、继续购买等表达。"
        />
        <StatCard
          label="退款/退出意向"
          value={summary.refund_intent_users ?? 0}
          icon="↩"
          color="red"
          href="/users?filter=refund_intent"
          description="用户聊天中出现退款、退费、不继续、退出、退群等表达。"
        />
        <StatCard
          label="流失用户"
          value={summary.lost_users}
          icon="📉"
          color="orange"
          href="/users?filter=lost"
          description="服务中，且超过 7 天没有用户入站消息的服务单；不再计入未跟进。"
        />
        <StatCard
          label="服务单总数"
          value={summary.service_cases_total}
          icon="📋"
          color="blue"
          href="/users"
          description="当前未删除服务单总数。"
        />
        <StatCard
          label="服务中"
          value={summary.service_cases_in_service}
          icon="🟢"
          color="green"
          href="/users?filter=in_service"
          description="状态为服务中的服务单。"
        />
        <StatCard
          label="今日打卡"
          value={summary.checkins_today}
          icon="✅"
          color="yellow"
          href="/checkins"
          description="今天提交的打卡记录数。"
        />
        <StatCard
          label="今日消息"
          value={summary.messages_today}
          icon="💬"
          color="purple"
          href="/users?filter=messaged_today"
          description="今天产生的企微入站和出站消息总数。"
        />
        <StatCard
          label="今日情绪预警"
          value={summary.emotional_users_today ?? 0}
          icon="🌧️"
          color="red"
          href="/users?filter=human_intervention"
          description="今天入站消息中，经语义识别出现明显负面情绪、执行挫败或心理风险线索的用户数。"
        />
        <StatCard
          label="待审核文件"
          value={summary.pending_documents}
          icon="📄"
          color="orange"
          href="/documents"
          description="每个服务单当前有效的最新文档里，状态为草稿或待审核的文件数。历史版本不计入。"
        />
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  color,
  href,
  description,
}: {
  label: string;
  value: number;
  icon: string;
  color: string;
  href: string;
  description: string;
}) {
  const colorClasses: Record<string, string> = {
    blue: "bg-blue-50 border-blue-200",
    green: "bg-green-50 border-green-200",
    yellow: "bg-yellow-50 border-yellow-200",
    purple: "bg-purple-50 border-purple-200",
    orange: "bg-orange-50 border-orange-200",
    red: "bg-red-50 border-red-200",
  };

  return (
    <Link
      href={href}
      title={description}
      className={`block rounded-xl border p-6 transition hover:-translate-y-0.5 hover:shadow-sm ${colorClasses[color] || "bg-zinc-50 border-zinc-200"}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-2xl">{icon}</span>
        <span className="text-3xl font-bold text-zinc-900">{value}</span>
      </div>
      <div className="mt-2 text-sm font-medium text-zinc-600">{label}</div>
      <p className="mt-2 line-clamp-2 text-xs leading-5 text-zinc-500">{description}</p>
    </Link>
  );
}
