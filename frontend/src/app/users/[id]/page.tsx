import Link from "next/link";
import { api, Checkin, Message, User } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

type Questionnaire = {
  fields: { key: string; label?: string; title?: string }[];
  latest_submission?: {
    answers_json: Record<string, unknown>;
    created_at: string;
  } | null;
};

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await api<User>(`/users/${id}`);
  const profile = user.latest_profile;
  const serviceCase = user.service_cases[0];
  const checkins = serviceCase ? await api<Checkin[]>(`/service-cases/${serviceCase.id}/checkins`).catch(() => []) : [];
  const messages = serviceCase ? await api<Message[]>(`/service-cases/${serviceCase.id}/messages`).catch(() => []) : [];
  const questionnaire = serviceCase ? await api<Questionnaire>(`/questionnaires/${serviceCase.questionnaire_token}`).catch(() => null) : null;
  const answers = questionnaire?.latest_submission?.answers_json ?? {};
  const answerLookup = buildAnswerLookup(questionnaire?.fields ?? [], answers);
  const latestParsed = checkins[0]?.parsed_json;
  const uniqueCheckinDays = new Set(checkins.map(c => c.checkin_date).filter(Boolean)).size;

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
        <h2 className="font-medium">基本信息</h2>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-4">
          <Info label="用户ID" value={String(user.id)} />
          <Info label="真实姓名" value={user.name} />
          <Info label="手机号" value={user.phone ?? "暂无数据"} />
          <Info label="企微ID" value={user.wecom_user_id ?? "暂无数据"} />
          <Info label="性别/年龄" value={profileText(profile?.profile_json, "gender_age") || answerByLabel(answerLookup, ["性别", "年龄"]) || "暂无数据"} />
          <Info label="身高/体重" value={profileText(profile?.profile_json, "height_weight") || answerByLabel(answerLookup, ["身高", "体重"]) || latestParsed?.weight || "暂无数据"} />
          <Info label="BMI" value={profileText(profile?.profile_json, "bmi") || answerByLabel(answerLookup, ["BMI", "bmi"]) || "暂无数据"} />
          <Info label="腰围" value={profileText(profile?.profile_json, "waist") || answerByLabel(answerLookup, ["腰围"]) || "暂无数据"} />
        </div>
      </section>

      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium">用户画像</h2>
          <span className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-600">来源：问卷自动生成</span>
        </div>
        {profile ? (
          <ProfileView profile={profile.profile_json} fallback={profile.summary} serviceCase={serviceCase} />
        ) : (
          <p className="mt-3 text-sm text-zinc-500">尚未生成用户画像。用户提交问卷后会自动生成一次，也可以在服务单详情手动触发。</p>
        )}
      </section>
      {serviceCase && (
        <section className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="font-medium">正在进行的方案</h2>
          <Link className="mt-3 block rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50" href={`/cases/${serviceCase.id}`}>
            {serviceCase.case_no} · {serviceCase.status}
          </Link>
          <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
            <Info label="人工介入" value={serviceCase.needs_human_intervention ? reasonText(serviceCase.intervention_reasons) : "暂无"} />
            <Info label="风险原因" value={serviceCase.risk_level === "high" ? reasonText(serviceCase.risk_flags) : "暂无"} />
            <Info label="AI自动回复" value={serviceCase.ai_auto_reply_enabled === false ? "关闭" : "开启"} />
            <Info label="续购意向" value={intentText(serviceCase.renewal_intent_flags)} />
            <Info label="退款/退出意向" value={intentText(serviceCase.refund_intent_flags)} />
          </div>
        </section>
      )}
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">打卡数据</h2>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Info label="体重变化" value={weightTrend(checkins)} />
          <Info label="睡眠变化" value={latestParsed?.sleep || "暂无数据"} />
          <Info label="情绪变化" value={latestParsed?.mood || "暂无数据"} />
          <Info label="饮水" value={latestParsed?.water || "暂无数据"} />
          <Info label="运动" value={latestParsed?.exercise || "暂无数据"} />
          <Info label="打卡天数" value={`${uniqueCheckinDays} 天`} />
        </div>
      </section>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">AI总结</h2>
        <p className="mt-3 max-h-40 overflow-y-auto text-sm leading-6 pr-1">{profile?.summary || "暂无数据"}</p>
      </section>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">购买与复购</h2>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Info label="购买记录" value="暂无数据" />
          <Info label="复购记录" value="暂无数据" />
          <Info label="客单价" value="暂无数据" />
        </div>
      </section>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">历史聊天摘要</h2>
        <p className="mt-3 text-sm text-zinc-600">{chatSummary(messages)}</p>
        <div className="mt-3 max-h-48 overflow-y-auto space-y-2 pr-1 text-sm">
          {messages.slice(-5).map((message) => (
            <div key={message.id} className="rounded-md border border-zinc-100 p-2">
              <span className="text-zinc-500">{message.sender_type === "user" ? "用户" : "机器人"}：</span>
              {message.content}
            </div>
          ))}
        </div>
      </section>
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">服务单</h2>
        <div className="mt-3 max-h-48 overflow-y-auto grid gap-2 pr-1 text-sm">
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

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-1 font-medium text-zinc-900">{value}</div>
    </div>
  );
}

function ProfileView({
  profile,
  fallback,
  serviceCase,
}: {
  profile: Record<string, unknown>;
  fallback: string;
  serviceCase?: User["service_cases"][number];
}) {
  const summary = text(profile.summary) || fallback;
  const items = [
    ["风险识别", serviceCase?.risk_level === "high" ? reasonText(serviceCase.risk_flags) : text(profile.risk_identification) || text(profile.risks) || "暂无明确风险"],
    ["减重带判断", text(profile.weight_loss_band) || text(profile.weight_band) || text(profile.fat_loss_stage) || "暂无数据"],
    ["当前状态", text(profile.state) || "暂无数据"],
    ["核心问题", text(profile.main_problem) || "暂无数据"],
    ["28 天目标", text(profile.day28_goal) || "暂无数据"],
    ["标签", arrayText(profile.tags) || text(profile.tags) || "暂无数据"],
    ["运营备注", text(profile.notes) || "暂无数据"],
  ];

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

function arrayText(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).join("、") : "";
}

function profileText(profile: Record<string, unknown> | undefined, key: string) {
  if (!profile) return "";
  const overview = profile.overview;
  if (overview && typeof overview === "object" && key in overview) {
    return text((overview as Record<string, unknown>)[key]);
  }
  return text(profile[key]);
}

function weightTrend(checkins: Checkin[]) {
  const weights = checkins
    .map((item) => item.parsed_json?.weight || "")
    .map((value) => Number(String(value).match(/\d+(\.\d+)?/)?.[0]))
    .filter((value) => Number.isFinite(value));
  if (weights.length < 2) return weights.length === 1 ? `${weights[0]}` : "暂无数据";
  const first = weights[weights.length - 1];
  const last = weights[0];
  return `${first} → ${last}（${(last - first).toFixed(1)}）`;
}

function chatSummary(messages: Message[]) {
  if (messages.length === 0) return "暂无数据";
  const inbound = messages.filter((message) => message.direction === "inbound");
  return `共 ${messages.length} 条聊天记录，用户发言 ${inbound.length} 条。`;
}

function reasonText(flags?: Array<Record<string, string>> | null) {
  if (!flags?.length) return "暂无";
  return flags
    .slice(-3)
    .map((item) => item.evidence ? `${item.reason || item.code}（${item.evidence}）` : item.reason || item.code)
    .join("；");
}

function intentText(flags?: Array<Record<string, string | boolean>> | null) {
  if (!flags?.length) return "暂无";
  const latest = flags[flags.length - 1];
  return typeof latest.evidence === "string" && latest.evidence ? latest.evidence : "已识别";
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  return formatChinaDateTime(value);
}

function buildAnswerLookup(fields: Questionnaire["fields"], answers: Record<string, unknown>) {
  const rows: Array<[string, string]> = [];
  for (const field of fields) {
    const key = field.key;
    const value = answers[key];
    if (value === undefined || value === null || value === "") continue;
    rows.push([field.title || field.label || key, answerText(value)]);
  }
  return rows;
}

function answerByLabel(rows: Array<[string, string]>, keywords: string[]) {
  const matched = rows
    .filter(([label]) => keywords.some((keyword) => label.toLowerCase().includes(keyword.toLowerCase())))
    .map(([label, value]) => `${label}：${value}`);
  return matched.join("；");
}

function answerText(value: unknown) {
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
