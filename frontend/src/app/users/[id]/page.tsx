import Link from "next/link";
import { api, Checkin, Message, User, UserDocument, UserProfile, UserStateProfile } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

const SERVICE_STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  questionnaire_submitted: "已提交问卷",
  generating: "文件生成中",
  documents_generated: "已生成文档",
  reviewing: "审核中",
  approved: "已批准",
  in_service: "服务中",
  completed: "已毕业",
  refunded: "已退款",
};

function statusLabel(status: string): string {
  return SERVICE_STATUS_LABELS[status] ?? status;
}

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await api<User>(`/users/${id}`);
  const profile = user.latest_profile;
  const stateProfile = user.latest_state_profile;
  const serviceCase = [...user.service_cases].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  )[0];
  const checkins = serviceCase ? await api<Checkin[]>(`/service-cases/${serviceCase.id}/checkins`).catch(() => []) : [];
  const messages = serviceCase ? await api<Message[]>(`/service-cases/${serviceCase.id}/messages`).catch(() => []) : [];
  const documents = serviceCase ? await api<UserDocument[]>(`/service-cases/${serviceCase.id}/documents`).catch(() => []) : [];
  const aiContextPreview = serviceCase
    ? await api<{ context: string }>(`/service-cases/${serviceCase.id}/ai-context-preview`).catch(() => null)
    : null;
  const latestParsed = checkins[0]?.parsed_json;
  const uniqueCheckinDays = new Set(checkins.map(c => c.checkin_date).filter(Boolean)).size;
  const energyDocument = documents.find((item) => item.document_type === "energy_restart_prescription");
  const executionDocument = documents.find((item) => item.document_type === "day28_execution_standard");
  const overview = recordValue(profile?.profile_json?.overview);
  const refreshedFrom = recordValue(profile?.profile_json?.refreshed_from);
  const profileSource = text(refreshedFrom.stage)
    ? `来源：进展刷新（${stageLabel(text(refreshedFrom.stage))}）`
    : "来源：问卷自动生成";

  return (
    <div className="page-stack">
      <div className="min-w-0">
        <h1 className="page-heading">{user.name}</h1>
        <p className="text-break page-subtitle">{user.phone ?? "无手机号"} · {user.wecom_user_id ?? "无聊天用户 ID"}</p>
        {user.deleted_at && (
          <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
            已删除：{formatDateTime(user.deleted_at)}
          </p>
        )}
      </div>

      {serviceCase && (
        <OperationsSummary
          serviceCase={serviceCase}
          profile={profile}
          stateProfile={stateProfile || undefined}
          checkins={checkins}
          documents={documents}
          uniqueCheckinDays={uniqueCheckinDays}
        />
      )}

      <section className="panel p-4 sm:p-5">
        <h2 className="font-semibold text-slate-950">基本信息</h2>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-4">
          <Info label="用户ID" value={String(user.id)} />
          <Info label="真实姓名" value={user.name} />
          <Info label="手机号" value={user.phone ?? "暂无数据"} />
          <Info label="聊天用户ID" value={user.wecom_user_id ?? "暂无数据"} />
          <Info label="性别/年龄" value={text(overview.gender_age) || "暂无数据"} />
          <Info label="身高/体重" value={text(overview.height_weight) || "暂无数据"} />
          <Info label="BMI" value={text(overview.bmi) || "暂无数据"} />
          <Info label="腰围" value={text(overview.waist) || text(overview.waist_bodyfat) || "暂无数据"} />
        </div>
      </section>

      <section className="panel p-4 sm:p-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="font-semibold text-slate-950">用户画像</h2>
          <span className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-600">{profileSource}</span>
        </div>
        {profile ? (
          <>
            <ProfileView profile={profile.profile_json} fallback={profile.summary} serviceCase={serviceCase} />
          </>
        ) : (
          <p className="mt-3 text-sm text-zinc-500">尚未生成用户画像。用户提交入营问卷后，系统会自动生成画像和交付文件。</p>
        )}
      </section>
      {stateProfile && (
        <section className="panel p-4 sm:p-5">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-semibold text-slate-950">当前用户状态</h2>
            <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">证据驱动更新</span>
          </div>
          <LongTermMemoryView state={stateProfile.state_json} />
          <StateProfileView state={stateProfile.state_json} updatedAt={stateProfile.updated_at} />
        </section>
      )}
      {(energyDocument || executionDocument) && (
        <section className="panel p-4 sm:p-5">
          <h2 className="font-semibold text-slate-950">交付文件摘要</h2>
          <DocumentHighlights energyDocument={energyDocument} executionDocument={executionDocument} />
        </section>
      )}
      {serviceCase && (
        <section className="panel p-4 sm:p-5">
          <h2 className="font-semibold text-slate-950">正在进行的方案</h2>
          <Link className="text-break mt-3 block rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50" href={`/cases/${serviceCase.id}`}>
            {serviceCase.case_no} · {statusLabel(serviceCase.status)}
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
      <section className="panel p-4 sm:p-5">
        <h2 className="font-semibold text-slate-950">打卡数据</h2>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Info label="体重变化" value={weightTrend(checkins)} />
          <Info label="睡眠变化" value={latestParsed?.sleep || "暂无数据"} />
          <Info label="情绪变化" value={latestParsed?.mood || "暂无数据"} />
          <Info label="饮水" value={latestParsed?.water || "暂无数据"} />
          <Info label="运动" value={latestParsed?.exercise || "暂无数据"} />
          <Info label="打卡天数" value={`${uniqueCheckinDays} 天`} />
        </div>
      </section>
      <section className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-zinc-500 sm:p-5">
        <h2 className="font-semibold">购买与复购</h2>
        <p className="mt-2 text-xs">订单数据尚未接入，仅预留展示位。</p>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Info label="购买记录" value="未接入" muted />
          <Info label="复购记录" value="未接入" muted />
          <Info label="客单价" value="未接入" muted />
        </div>
      </section>
      <section className="panel p-4 sm:p-5">
        <h2 className="font-semibold text-slate-950">历史聊天摘要</h2>
        <p className="mt-3 text-sm text-zinc-600">{chatSummary(messages)}</p>
        <div className="mt-3 max-h-48 overflow-y-auto space-y-2 pr-1 text-sm">
          {messages.slice(-5).map((message) => (
            <div key={message.id} className="rounded-md border border-zinc-100 p-2">
              <span className="text-zinc-500">{
                message.sender_type === "bot" ? "燃人.AI"
                  : message.sender_type === "staff" ? (message.display_sender_name || "班主任")
                  : message.direction === "outbound" ? "运营"
                  : (message.display_sender_name || "客户")
              }：</span>
              <span className="text-break whitespace-pre-wrap">{message.content}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="panel p-4 sm:p-5">
        <h2 className="font-semibold text-slate-950">服务单</h2>
        <div className="mt-3 max-h-48 overflow-y-auto grid gap-2 pr-1 text-sm">
          {user.service_cases.map((item) => (
            <Link key={item.id} className="text-break rounded-md border border-zinc-200 px-3 py-2 hover:bg-zinc-50" href={`/cases/${item.id}`}>
              {item.case_no} · {item.group_id} · {statusLabel(item.status)}
            </Link>
          ))}
        </div>
      </section>
      {profile && (
        <section className="panel p-4 sm:p-5">
          <h2 className="font-semibold text-slate-950">调试信息</h2>
          <p className="mt-1 text-xs text-zinc-500">供排查 AI 回复上下文使用，日常运营可忽略。</p>
          <AiContextPreview context={aiContextPreview?.context} />
        </section>
      )}
    </div>
  );
}

function Info({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className={`rounded-md border p-3 ${muted ? "border-zinc-200 bg-zinc-100 text-zinc-400" : "border-zinc-200 bg-zinc-50"}`}>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`text-break mt-1 font-medium ${muted ? "text-zinc-400" : "text-zinc-900"}`}>{value}</div>
    </div>
  );
}

function OperationsSummary({
  serviceCase,
  profile,
  stateProfile,
  checkins,
  documents,
  uniqueCheckinDays,
}: {
  serviceCase: User["service_cases"][number];
  profile?: UserProfile | null;
  stateProfile?: UserStateProfile;
  checkins: Checkin[];
  documents: UserDocument[];
  uniqueCheckinDays: number;
}) {
  const profileJson = profile?.profile_json || {};
  const domains = recordValue(stateProfile?.state_json?.domains);
  const focus = currentActionFocus(domains);
  const riskText = serviceCase.needs_human_intervention
    ? reasonText(serviceCase.intervention_reasons) || "存在待关注事项"
    : serviceCase.risk_level === "high"
      ? reasonText(serviceCase.risk_flags)
      : "暂无高风险";
  const latestChange = latestStateChange(domains) || latestCheckinSignal(checkins);
  const nextAction =
    text(focus.action)
    || firstText(recordValue(profileJson.need_analysis).next_actions)
    || text(profileJson.notes)
    || "按当前方案继续观察，优先保持打卡与反馈连续。";
  const sentDocs = documents.filter((document) => document.status === "sent").length;

  return (
    <section className="overflow-hidden rounded-xl border border-cyan-200 bg-cyan-50/60">
      <div className="border-b border-cyan-100 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-950">运营摘要</h2>
            <p className="mt-1 text-sm leading-6 text-slate-600">系统综合画像、打卡、聊天和文档后的运营视图，不是新的独立 AI 诊断。</p>
          </div>
          <span className="rounded-full border border-cyan-200 bg-white px-3 py-1 text-xs font-semibold text-cyan-800">
            {serviceStageText(serviceCase)}
          </span>
        </div>
      </div>
      <div className="grid gap-3 p-5 lg:grid-cols-5">
        <SummaryMetric label="当前重点" value={text(focus.label) || "暂无明确重点"} tone="cyan" />
        <SummaryMetric label="关键风险" value={riskText} tone={riskText === "暂无高风险" ? "slate" : "rose"} />
        <SummaryMetric label="最近变化" value={latestChange || "暂无新变化"} tone="emerald" />
        <SummaryMetric label="交付/打卡" value={`${sentDocs}/${documents.length} 份已发 · 打卡 ${uniqueCheckinDays} 天`} tone="blue" />
        <SummaryMetric label="下一步动作" value={nextAction} tone="amber" />
      </div>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "cyan" | "rose" | "emerald" | "blue" | "amber" | "slate";
}) {
  const toneClass: Record<"cyan" | "rose" | "emerald" | "blue" | "amber" | "slate", string> = {
    cyan: "border-cyan-100 bg-cyan-50 text-cyan-950",
    rose: "border-rose-100 bg-rose-50 text-rose-900",
    emerald: "border-emerald-100 bg-emerald-50 text-emerald-950",
    blue: "border-blue-100 bg-blue-50 text-blue-950",
    amber: "border-amber-100 bg-amber-50 text-amber-950",
    slate: "border-slate-100 bg-slate-50 text-slate-900",
  };
  return (
    <div className={`rounded-lg border p-3 ${toneClass[tone]}`}>
      <div className="text-xs font-medium opacity-70">{label}</div>
      <div className="text-break mt-1 line-clamp-4 text-sm font-semibold leading-6">{value}</div>
    </div>
  );
}

function serviceStageText(serviceCase: User["service_cases"][number]) {
  const statusMap: Record<string, string> = {
    created: "未建档",
    questionnaire_submitted: "已提交问卷",
    generating: "文件生成中",
    documents_generated: "文件已生成",
    reviewing: "待审核",
    in_service: "服务中",
    completed: "已毕业",
    refunded: "已退款",
  };
  const status = statusMap[serviceCase.status] || serviceCase.status;
  const start = serviceCase.service_plan_start_date || serviceCase.started_at || serviceCase.created_at;
  if (!start) return status;
  const days = Math.floor((Date.now() - new Date(start).getTime()) / 86400000) + 1;
  return days >= 1 ? `${status} · 第 ${days} 天` : `${status} · 计划未开始`;
}

function currentActionFocus(domains: Record<string, unknown>) {
  const domain = recordValue(domains.action_focus);
  const value = recordValue(recordValue(domain.current).value);
  const focus = recordValue(value.current_focus);
  return {
    label: text(focus.label) || text(value.label),
    action: text(focus.action) || text(value.action),
    score: focus.score ?? value.score,
  };
}

function latestStateChange(domains: Record<string, unknown>) {
  for (const key of ["outcome_changes", "body_feedback", "product_usage", "risk_profile"]) {
    const domain = recordValue(domains[key]);
    const current = recordValue(domain.current);
    const value = recordValue(current.value);
    const formatted = formatStateValue(value);
    if (formatted && formatted !== "暂无数据") return formatted;
  }
  return "";
}

function latestCheckinSignal(checkins: Checkin[]) {
  const latest = checkins[0]?.parsed_json;
  if (!latest) return "";
  const parts = [
    latest.weight ? `体重 ${latest.weight}` : "",
    latest.sleep ? `睡眠 ${latest.sleep}` : "",
    latest.mood ? `情绪 ${latest.mood}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function firstText(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).find(Boolean) || "";
  }
  return text(value);
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
  const tags = Array.isArray(profile.tags) ? profile.tags.filter(Boolean) : [];
  const needAnalysis = recordValue(profile.need_analysis);
  const hasNeedAnalysis = Object.keys(needAnalysis).length > 0;
  const baseFacts = [
    ["减重带", profileText(profile, "weight_loss_band") || text(profile.weight_loss_band) || "暂无数据"],
    ["28天目标", text(profile.day28_goal) || "暂无数据"],
    ["画像标签", tags.length ? tags.map(String).join("、") : arrayText(profile.tags) || "暂无数据"],
  ];
  const serviceJudgement = [
    ["当前状态", text(profile.state) || "暂无数据"],
    ["核心问题", text(profile.main_problem) || "暂无数据"],
    ["风控风险", serviceCase?.risk_level === "high" ? reasonText(serviceCase.risk_flags) : "暂无明确风险"],
  ];
  const coachingFocus = [
    ["优先需求", text(needAnalysis.priority_need) || "暂无数据"],
    ["下一步动作", firstText(needAnalysis.next_actions) || text(profile.notes) || "暂无数据"],
    ["陪跑建议", text(profile.notes) || "暂无数据"],
  ];

  return (
    <div className="mt-3 space-y-4 text-sm leading-6">
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
        <div className="text-xs font-medium text-slate-500">一句话画像</div>
        <p className="text-break mt-2 text-sm leading-7 text-slate-800">{summary}</p>
        {tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {tags.map((tag) => (
              <span key={String(tag)} className="inline-flex items-center rounded-md bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700 ring-1 ring-inset ring-blue-700/10">
                {String(tag)}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <ProfileGroup title="基础事实" items={baseFacts} />
        <ProfileGroup title="服务判断" items={serviceJudgement} />
        <ProfileGroup title="陪跑重点" items={coachingFocus} />
      </div>

      {hasNeedAnalysis && (
        <details className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50">
          <summary className="flex cursor-pointer items-center justify-between px-4 py-3 text-sm font-medium text-zinc-700">
            <span>完整健康需求识别</span>
            <span className="text-xs font-normal text-zinc-400">展开查看表层需求、深层需求和证据</span>
          </summary>
          <div className="border-t border-zinc-200 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-3">
              <NeedList label="表层表达需求" items={needAnalysis.surface_needs} />
              <NeedList label="深层服务需求" items={needAnalysis.deep_needs} />
              <div className="rounded-md border border-zinc-200 bg-white p-3">
                <div className="text-xs font-medium text-zinc-500">最优先满足需求</div>
                <div className="mt-1 font-semibold text-cyan-900">{String(needAnalysis.priority_need || "暂无")}</div>
              </div>
            </div>
            <div className="space-y-3">
              <NeedList label="系统交付方案/动作" items={needAnalysis.system_outputs} />
              <NeedList label="接下来最该推进动作" items={needAnalysis.next_actions} />
              <NeedList label="依据/判定证据" items={needAnalysis.evidence_notes} />
            </div>
          </div>
          </div>
        </details>
      )}
    </div>
  );
}

function ProfileGroup({ title, items }: { title: string; items: string[][] }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3">
      <div className="text-xs font-semibold text-zinc-500">{title}</div>
      <div className="mt-2 space-y-2">
        {items.map(([label, value]) => (
          <div key={label}>
            <div className="text-[11px] text-zinc-400">{label}</div>
            <div className="text-break mt-0.5 text-sm font-medium leading-6 text-zinc-900">{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function NeedList({ label, items }: { label: string; items: unknown }) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  return (
    <div className="rounded-md border border-zinc-200 bg-white p-3">
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      {list.length > 0 ? (
        <ul className="mt-1 space-y-1 text-sm list-disc pl-4 text-zinc-700">
          {list.map((item, idx) => (
            <li key={idx} className="text-break">{String(item)}</li>
          ))}
        </ul>
      ) : (
        <div className="mt-1 text-zinc-400">暂无数据</div>
      )}
    </div>
  );
}

function AiContextPreview({ context }: { context?: string }) {
  const contextText = context || "暂无 AI 上下文。";
  return (
    <details className="group mt-3 rounded-lg border border-slate-200 bg-slate-50 text-slate-900">
      <summary className="flex cursor-pointer items-center justify-between px-4 py-3 select-none font-semibold focus:outline-none">
        <span className="flex items-center gap-2 text-sm text-slate-700">
          <span className="h-2 w-2 rounded-full bg-slate-400" />
          AI 视角：真实提示词上下文预览
        </span>
        <span className="text-xs text-zinc-500 group-open:hidden">点击展开</span>
        <span className="text-xs text-zinc-500 hidden group-open:inline">点击收起</span>
      </summary>
      <div className="text-break max-w-full overflow-x-auto whitespace-pre-wrap border-t border-slate-200 px-4 py-4 font-mono text-xs leading-6 text-slate-600 select-all">
        {contextText}
      </div>
    </details>
  );
}

function LongTermMemoryView({ state }: { state: Record<string, unknown> }) {
  const domains = recordValue(state.domains);
  const preferences = memoryItems(recordValue(domains.personal_preferences), 4);
  const habits = memoryItems(recordValue(domains.lifestyle_habits), 4);
  const risk = memoryItems(recordValue(domains.risk_profile), 2);
  const hasMemory = preferences.length > 0 || habits.length > 0 || risk.length > 0;
  if (!hasMemory) return null;

  return (
    <div className="mt-3 grid gap-3 md:grid-cols-3">
      <MemoryCard title="长期偏好" items={preferences} empty="暂无明确偏好" />
      <MemoryCard title="稳定习惯" items={habits} empty="暂无稳定习惯" />
      <MemoryCard title="注意边界" items={risk} empty="暂无特殊风险" tone="rose" />
    </div>
  );
}

function MemoryCard({
  title,
  items,
  empty,
  tone = "slate",
}: {
  title: string;
  items: string[];
  empty: string;
  tone?: "slate" | "rose";
}) {
  const toneClass = tone === "rose"
    ? "border-rose-100 bg-rose-50 text-rose-950"
    : "border-slate-100 bg-slate-50 text-slate-950";
  return (
    <div className={`rounded-lg border p-3 ${toneClass}`}>
      <div className="text-xs font-semibold opacity-70">{title}</div>
      {items.length > 0 ? (
        <ul className="mt-2 space-y-1.5 text-sm leading-5">
          {items.map((item) => (
            <li key={item} className="text-break line-clamp-2">{item}</li>
          ))}
        </ul>
      ) : (
        <div className="mt-2 text-sm opacity-60">{empty}</div>
      )}
    </div>
  );
}

function StateProfileView({
  state,
  updatedAt,
}: {
  state: Record<string, unknown>;
  updatedAt: string;
}) {
  const domains = recordValue(state.domains);
  const primaryKeys = ["action_focus", "personal_preferences", "lifestyle_habits", "body_feedback", "risk_profile"];
  const visibleDomains = [
    "action_focus",
    "goals",
    "need_analysis",
    "personal_preferences",
    "lifestyle_habits",
    "diet_structure",
    "rhythm_structure",
    "product_usage",
    "body_feedback",
    "risk_profile",
    "outcome_changes",
  ]
    .map((key) => [key, recordValue(domains[key])] as const)
    .filter(([, domain]) => Object.keys(recordValue(domain.current)).length || (Array.isArray(domain.history) && domain.history.length > 0));
  const primaryDomains = visibleDomains.filter(([key]) => primaryKeys.includes(key));
  const secondaryDomains = visibleDomains.filter(([key]) => !primaryKeys.includes(key));

  return (
    <div className="mt-3 space-y-4 text-sm">
      <p className="text-xs text-zinc-500">
        默认只展示运营接话最常用的信息；完整字段和证据链可展开查看。
        最近更新：{formatDateTime(updatedAt)}
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {primaryDomains.map(([key, domain]) => (
          <StateDomainCard key={key} domain={domain} />
        ))}
      </div>
      {secondaryDomains.length > 0 && (
        <details className="rounded-lg border border-zinc-200 bg-zinc-50">
          <summary className="flex cursor-pointer items-center justify-between px-4 py-3 text-sm font-medium text-zinc-700">
            <span>查看完整状态字段</span>
            <span className="text-xs font-normal text-zinc-400">{secondaryDomains.length} 个补充字段</span>
          </summary>
          <div className="grid gap-3 border-t border-zinc-200 p-3 lg:grid-cols-2">
            {secondaryDomains.map(([key, domain]) => (
              <StateDomainCard key={key} domain={domain} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function StateDomainCard({ domain }: { domain: Record<string, unknown> }) {
  const current = recordValue(domain.current);
  const currentValue = recordValue(current.value);
  const history = Array.isArray(domain.history) ? domain.history.map(recordValue).filter(Boolean).slice(-3).reverse() : [];
  const label = text(domain.label) || "状态";
  const evidence = text(current.evidence);

  if (label === "当前行动重点") {
    return <ActionFocusCard currentValue={currentValue} current={current} history={history} evidence={evidence} />;
  }

  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-zinc-500">{label}</div>
        {text(current.updated_at) && <div className="text-[11px] text-zinc-400">{formatDateTime(text(current.updated_at))}</div>}
      </div>
      {Object.keys(currentValue).length > 0 ? (
        <div className="mt-2 space-y-1">
          {Object.entries(currentValue).slice(0, 5).map(([key, value]) => (
            <div key={key} className="leading-6">
              <span className="text-zinc-500">{stateFieldLabel(key)}：</span>
              <span className="text-break">{formatStateValue(value)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 text-zinc-500">暂无当前状态</div>
      )}
      {evidence && <p className="text-break mt-2 rounded bg-white px-2 py-1 text-xs leading-5 text-zinc-500">依据：{evidence}</p>}
      {history.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-blue-600">查看证据链</summary>
          <div className="mt-2 space-y-2">
            {history.map((item, index) => (
              <div key={`${text(item.evidence)}-${index}`} className="rounded border border-zinc-100 bg-white p-2 text-xs leading-5">
                <div className="text-zinc-400">{formatDateTime(text(item.occurred_at))}</div>
                <div className="text-break mt-1 text-zinc-600">{text(item.evidence) || formatStateValue(item.value)}</div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function ActionFocusCard({
  currentValue,
  current,
  history,
  evidence,
}: {
  currentValue: Record<string, unknown>;
  current: Record<string, unknown>;
  history: Record<string, unknown>[];
  evidence: string;
}) {
  const focus = recordValue(currentValue.current_focus);
  const candidates = recordValue(currentValue.candidates);
  const sortedCandidates = Object.values(candidates)
    .map(recordValue)
    .filter((item) => text(item.label))
    .sort((a, b) => Number(b.score ?? 0) - Number(a.score ?? 0))
    .slice(0, 3);

  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-zinc-500">当前行动重点</div>
        {text(current.updated_at) && <div className="text-[11px] text-zinc-400">{formatDateTime(text(current.updated_at))}</div>}
      </div>
      {text(focus.label) ? (
        <div className="mt-2 space-y-1 leading-6">
          <div className="text-break"><span className="text-zinc-500">重点：</span>{text(focus.label)}</div>
          <div className="text-break"><span className="text-zinc-500">动作：</span>{text(focus.action) || "暂无动作"}</div>
          <div><span className="text-zinc-500">分数：</span>{String(focus.score ?? "暂无")}</div>
          {text(focus.soft_lock_until) && <div><span className="text-zinc-500">稳定期至：</span>{formatDateTime(text(focus.soft_lock_until))}</div>}
        </div>
      ) : (
        <div className="mt-2 text-zinc-500">暂无当前行动重点</div>
      )}
      {sortedCandidates.length > 0 && (
        <div className="mt-3 rounded bg-white px-2 py-2 text-xs leading-5 text-zinc-500">
          <div className="font-medium text-zinc-600">候选池</div>
          {sortedCandidates.map((item) => (
          <div key={text(item.key) || text(item.label)} className="text-break">{text(item.label)}：{String(item.score ?? "0")}</div>
          ))}
        </div>
      )}
      {evidence && <p className="text-break mt-2 rounded bg-white px-2 py-1 text-xs leading-5 text-zinc-500">依据：{evidence}</p>}
      {history.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-blue-600">查看变更记录</summary>
          <div className="mt-2 space-y-2">
            {history.map((item, index) => (
              <div key={`${text(item.evidence)}-${index}`} className="rounded border border-zinc-100 bg-white p-2 text-xs leading-5">
                <div className="text-zinc-400">{formatDateTime(text(item.occurred_at))}</div>
                <div className="text-break mt-1 text-zinc-600">{text(item.evidence) || formatStateValue(item.value)}</div>
              </div>
            ))}
          </div>
        </details>
      )}
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
      <div className="text-break mt-1 whitespace-pre-wrap">{value}</div>
    </div>
  );
}

function SummaryListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3">
      <div className="text-xs font-medium text-zinc-500">{title}</div>
      <ul className="mt-2 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-break ml-5 list-disc">{item}</li>
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

function stageLabel(stage: string) {
  const map: Record<string, string> = {
    day3: "第3天", day7: "第7天", day14: "第14天", day21: "第21天", day28: "第28天",
  };
  if (map[stage]) return map[stage];
  const m = stage.match(/^day(\d+)$/);
  return m ? `第${m[1]}天` : stage;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stateFieldLabel(key: string) {
  const labels: Record<string, string> = {
    name: "姓名",
    preferred_name: "称呼",
    gender: "性别",
    age: "年龄",
    height_cm: "身高",
    weight_kg: "体重",
    bmi: "BMI",
    waist: "腰围",
    bodyfat: "体脂率",
    weight_loss_band: "减重带",
    main_needs: "服务需求",
    day28_expectation: "28天期待",
    day28_goal: "28天目标",
    main_problem: "核心问题",
    current_focus: "当前重点",
    candidates: "候选池",
    surface_needs: "表层需求",
    deep_needs: "深层需求",
    priority_need: "优先需求",
    system_outputs: "系统输出",
    next_actions: "下一步动作",
    evidence_notes: "需求证据",
    health_note: "健康提醒",
    risks: "风险识别",
    adherence_worry: "坚持风险",
    pressure_reaction: "压力反应",
    hobby: "兴趣爱好",
    activity_preference: "活动偏好",
    food_preference: "食物偏好",
    communication_preference: "沟通偏好",
    motivation_preference: "激励偏好",
    meal_pattern: "饮食习惯",
    schedule_constraint: "时间约束",
    work_routine: "工作节奏",
    exercise_habit: "运动习惯",
    sleep_habit: "睡眠习惯",
    social_context: "社交/家庭场景",
    other: "其他",
    diet_pattern: "饮食结构",
    profile_state: "状态判断",
    product_schedule: "产品执行",
    discomfort_feedback: "不适反馈",
    energy_change: "精神变化",
    sleep_change: "睡眠变化",
    bowel_change: "排便变化",
    weight_change: "体重变化",
    waist_or_body_change: "腰围/体感",
    weight_result: "体重结果",
    waist_result: "腰围结果",
    challenge_goal_result: "目标结果",
  };
  return labels[key] ?? key;
}

function formatStateValue(value: unknown): string {
  if (value === null || value === undefined) return "暂无数据";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(formatStateValue).filter(Boolean).join("、");
  }
  const record = recordValue(value);
  return text(record.value) || Object.values(record).map(formatStateValue).filter(Boolean).join("；") || "暂无数据";
}

function memoryItems(domain: Record<string, unknown>, limit: number) {
  const current = recordValue(domain.current);
  const value = recordValue(current.value);
  return Object.entries(value)
    .map(([key, item]) => {
      const record = recordValue(item);
      const rawValue = Object.prototype.hasOwnProperty.call(record, "value") ? record.value : item;
      const formatted = formatStateValue(rawValue);
      return formatted && formatted !== "暂无数据" ? `${stateFieldLabel(key)}：${formatted}` : "";
    })
    .filter(Boolean)
    .slice(0, limit);
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
