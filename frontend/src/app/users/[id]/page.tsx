import Link from "next/link";
import { api, Checkin, Message, User, UserDocument } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await api<User>(`/users/${id}`);
  const profile = user.latest_profile;
  const serviceCase = [...user.service_cases].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  )[0];
  const checkins = serviceCase ? await api<Checkin[]>(`/service-cases/${serviceCase.id}/checkins`).catch(() => []) : [];
  const messages = serviceCase ? await api<Message[]>(`/service-cases/${serviceCase.id}/messages`).catch(() => []) : [];
  const documents = serviceCase ? await api<UserDocument[]>(`/service-cases/${serviceCase.id}/documents`).catch(() => []) : [];
  const latestParsed = checkins[0]?.parsed_json;
  const uniqueCheckinDays = new Set(checkins.map(c => c.checkin_date).filter(Boolean)).size;
  const energyDocument = documents.find((item) => item.document_type === "energy_restart_prescription");
  const executionDocument = documents.find((item) => item.document_type === "day28_execution_standard");
  const overview = recordValue(profile?.profile_json?.overview);

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
          <Info label="性别/年龄" value={text(overview.gender_age) || "暂无数据"} />
          <Info label="身高/体重" value={text(overview.height_weight) || "暂无数据"} />
          <Info label="BMI" value={text(overview.bmi) || "暂无数据"} />
          <Info label="腰围" value={text(overview.waist) || text(overview.waist_bodyfat) || "暂无数据"} />
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
          <p className="mt-3 text-sm text-zinc-500">尚未生成用户画像。用户提交入营问卷后，系统会自动生成画像和交付文件。</p>
        )}
      </section>
      {(energyDocument || executionDocument) && (
        <section className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="font-medium">交付文件摘要</h2>
          <DocumentHighlights energyDocument={energyDocument} executionDocument={executionDocument} />
        </section>
      )}
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
      <section className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-zinc-500">
        <h2 className="font-medium">购买与复购</h2>
        <p className="mt-2 text-xs">订单数据尚未接入，仅预留展示位。</p>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Info label="购买记录" value="未接入" muted />
          <Info label="复购记录" value="未接入" muted />
          <Info label="客单价" value="未接入" muted />
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

function Info({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className={`rounded-md border p-3 ${muted ? "border-zinc-200 bg-zinc-100 text-zinc-400" : "border-zinc-200 bg-zinc-50"}`}>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`mt-1 font-medium ${muted ? "text-zinc-400" : "text-zinc-900"}`}>{value}</div>
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
    ["风控风险", serviceCase?.risk_level === "high" ? reasonText(serviceCase.risk_flags) : "暂无明确风险"],
    ["减重带判断", profileText(profile, "weight_loss_band") || text(profile.weight_loss_band) || "暂无数据"],
    ["当前状态", text(profile.state) || "暂无数据"],
    ["核心问题", text(profile.main_problem) || "暂无数据"],
    ["28 天目标", text(profile.day28_goal) || "暂无数据"],
    ["标签", arrayText(profile.tags) || text(profile.tags) || "暂无数据"],
    ["AI陪跑建议", text(profile.notes) || "暂无数据"],
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

function DocumentHighlights({
  energyDocument,
  executionDocument,
}: {
  energyDocument?: UserDocument;
  executionDocument?: UserDocument;
}) {
  const oneSentence = blockText(energyDocument, "one_sentence");
  const judgementPoints = blockItems(energyDocument, "judgement_points");
  const currentStateSegments = splitCurrentState(blockText(energyDocument, "current_state"));
  const moduleDiagnosis = blockText(energyDocument, "module_diagnosis");
  const successPath = blockText(energyDocument, "success_path");
  const nonNegotiable = blockText(energyDocument, "non_negotiable");
  const principle = blockText(executionDocument, "principle");
  const dailyCheckin = splitChecklist(blockText(executionDocument, "daily_checkin"));

  return (
    <div className="mt-3 space-y-4 text-sm leading-6">
      {oneSentence && <SummaryTextCard title="一句话结论" value={oneSentence} />}
      {judgementPoints.length > 0 && <SummaryListCard title="判断依据" items={judgementPoints} />}
      {(currentStateSegments.state || currentStateSegments.insulin || currentStateSegments.leptin || currentStateSegments.hormone || currentStateSegments.priority || moduleDiagnosis) && (
        <div className="grid gap-3 md:grid-cols-2">
          {currentStateSegments.state && <SummaryTextCard title="当前身体状态" value={currentStateSegments.state} />}
          {(currentStateSegments.insulin || currentStateSegments.leptin || currentStateSegments.hormone) && (
            <SummaryTextCard
              title="胰岛素 / 瘦素 / 激素判断"
              value={[
                currentStateSegments.insulin,
                currentStateSegments.leptin,
                currentStateSegments.hormone,
              ].filter(Boolean).join("\n")}
            />
          )}
          {currentStateSegments.priority && <SummaryTextCard title="当前最该先抓的方向" value={currentStateSegments.priority} />}
          {moduleDiagnosis && <SummaryTextCard title="模块诊断" value={moduleDiagnosis} />}
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {successPath && <SummaryTextCard title="成功路径" value={successPath} />}
        {nonNegotiable && <SummaryTextCard title="最不能打折的一件事" value={nonNegotiable} />}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {principle && <SummaryTextCard title="执行总原则" value={principle} />}
        {dailyCheckin.length > 0 && <SummaryListCard title="每日打卡项" items={dailyCheckin} />}
      </div>
    </div>
  );
}

function SummaryTextCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="text-xs font-medium text-zinc-500">{title}</div>
      <div className="mt-1 whitespace-pre-wrap">{value}</div>
    </div>
  );
}

function SummaryListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="text-xs font-medium text-zinc-500">{title}</div>
      <ul className="mt-2 space-y-1">
        {items.map((item) => (
          <li key={item} className="ml-5 list-disc">{item}</li>
        ))}
      </ul>
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

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function blockByKey(document: UserDocument | undefined, key: string) {
  const blocks = Array.isArray(document?.content_json?.blocks) ? document?.content_json?.blocks : [];
  return blocks.find((block) => block.key === key);
}

function blockText(document: UserDocument | undefined, key: string) {
  const block = blockByKey(document, key);
  return typeof block?.content === "string" ? block.content.trim() : "";
}

function blockItems(document: UserDocument | undefined, key: string) {
  const block = blockByKey(document, key);
  return Array.isArray(block?.items)
    ? block.items.map((item) => String(item).trim()).filter(Boolean)
    : [];
}

function splitChecklist(value: string) {
  return value
    .split("\n")
    .map((item) => item.replace(/^\d+[\.\、]\s*/, "").trim())
    .filter(Boolean);
}

function splitCurrentState(value: string) {
  const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
  const stateLines: string[] = [];
  let insulin = "";
  let leptin = "";
  let hormone = "";
  let priority = "";
  for (const line of lines) {
    if (line.startsWith("胰岛素抵抗判断：")) {
      insulin = line.replace("胰岛素抵抗判断：", "").trim();
      continue;
    }
    if (line.startsWith("瘦素敏感判断：")) {
      leptin = line.replace("瘦素敏感判断：", "").trim();
      continue;
    }
    if (line.startsWith("激素阶段影响判断：")) {
      hormone = line.replace("激素阶段影响判断：", "").trim();
      continue;
    }
    if (line.startsWith("当前最该先抓的方向：")) {
      priority = line.replace("当前最该先抓的方向：", "").trim();
      continue;
    }
    stateLines.push(line);
  }
  return {
    state: stateLines.join("\n"),
    insulin,
    leptin,
    hormone,
    priority,
  };
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
