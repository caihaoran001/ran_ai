import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from "lucide-react";
import {
  api,
  User,
  UserOperationsFact,
  UserOperationsSnapshot,
  UserProfile,
  UserStateProfile,
} from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";
import { HealthProfilePanel } from "@/components/health-profile-panel";
import { UserRecordDetails } from "./user-record-details";

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const user = await api<User>(`/users/${id}`);
  const profile = user.latest_profile;
  const stateProfile = user.latest_state_profile;
  const serviceCase = [...user.service_cases].sort(
    (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
  )[0];
  const customerType = text(recordValue(profile?.profile_json).customer_type);

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div className="min-w-0">
            <h1 className="page-heading">{user.name}</h1>
            <p className="text-break page-subtitle">
              {user.phone ?? "未留手机号"} · {user.wecom_user_id ?? "未绑定聊天用户 ID"}
            </p>
            {user.deleted_at && (
              <p className="mt-2 inline-flex rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-xs font-bold text-rose-700">
                已删除：{formatDateTime(user.deleted_at)}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {customerType && <CustomerTypeBadge type={customerType} />}
            {serviceCase && <span className="status-pill pill-info">{serviceStageText(serviceCase)}</span>}
          </div>
        </div>
      </div>

      {serviceCase && (
        <OperationsSummary
          snapshot={user.operations_snapshot}
          stateProfile={stateProfile}
        />
      )}

      {profile || stateProfile ? (
        <PersonalPortraitPanel profile={profile} stateProfile={stateProfile} />
      ) : (
        <div className="empty-state">尚未形成个人画像。提交入营问卷后，这里会显示服务基线和动态事实。</div>
      )}

      {serviceCase && <HealthProfilePanel profile={serviceCase.health_profile} />}

      {serviceCase && (
        <UserRecordDetails serviceCase={serviceCase} serviceCases={user.service_cases} />
      )}
    </div>
  );
}

function OperationsSummary({
  snapshot,
  stateProfile,
}: {
  snapshot?: UserOperationsSnapshot | null;
  stateProfile?: UserStateProfile | null;
}) {
  const domains = recordValue(recordValue(stateProfile?.state_json).domains);
  const currentStates = currentDomainItems(recordValue(domains.service_state), 3);
  const tone = attentionTone(snapshot?.attention_code);
  const AttentionIcon = tone.Icon;

  return (
    <section className="panel overflow-hidden" aria-labelledby="operations-heading">
      <div className="panel-header-clean flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="operations-heading" className="panel-title">当前运营状态</h2>
          <p className="panel-subtitle">先看是否需要人工介入，再看用户眼下卡在哪里。</p>
        </div>
        <span className={`status-pill ${tone.pill}`}>{snapshot?.attention_label || "状态待确认"}</span>
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1.15fr)_minmax(18rem,0.85fr)]">
        <div className={`min-w-0 px-5 py-5 sm:px-6 ${tone.surface}`}>
          <div className="flex items-start gap-3">
            <AttentionIcon className={`mt-0.5 h-5 w-5 shrink-0 ${tone.icon}`} aria-hidden="true" />
            <div className="min-w-0">
              <div className="text-xs font-bold text-slate-600">人工关注判断</div>
              <p className="text-break mt-1 max-w-[68ch] text-sm font-semibold leading-6 text-slate-950">
                {snapshot?.attention_reason || "尚无可用判断，请进入服务单核对。"}
              </p>
            </div>
          </div>
        </div>

        <div className="min-w-0 border-t border-slate-200 px-5 py-5 sm:px-6 lg:border-l lg:border-t-0">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-bold text-slate-600">当前状态与阻塞</div>
            {stateProfile?.updated_at && (
              <span className="shrink-0 text-xs text-slate-500">{formatDateTime(stateProfile.updated_at)}</span>
            )}
          </div>
          {currentStates.length > 0 ? (
            <dl className="mt-2 space-y-2">
              {currentStates.map((item) => (
                <div key={`${item.key}-${item.value}`} className="grid min-w-0 gap-0.5 sm:grid-cols-[7rem_1fr] sm:gap-3">
                  <dt className="text-xs font-semibold text-slate-500">{stateFieldLabel(item.key)}</dt>
                  <dd className="text-break text-sm font-semibold leading-5 text-slate-900">{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="mt-2 text-sm leading-6 text-slate-600">暂无明确的当前阻塞或待解决事项。</p>
          )}
        </div>
      </div>

      <dl className="grid divide-y divide-slate-200 border-t border-slate-200 text-sm sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <OperationFact label="班主任确认安排" fact={snapshot?.confirmed_guidance} empty="尚无班主任确认的专属安排" />
        <OperationFact label="最近动态事实" fact={snapshot?.latest_change} empty="暂无新的动态事实" />
        <div className="min-w-0 px-5 py-4 sm:px-6">
          <dt className="text-xs font-bold text-slate-500">打卡进度</dt>
          <dd className="mt-1 text-sm font-bold text-slate-900">已打卡 {snapshot?.checkin_days ?? 0} 天</dd>
          <p className="mt-1 text-xs text-slate-500">
            {snapshot?.last_checkin_at ? `最近 ${formatDateTime(snapshot.last_checkin_at)}` : "当前服务单尚无有效打卡"}
          </p>
        </div>
      </dl>
    </section>
  );
}

function OperationFact({
  label,
  fact,
  empty,
}: {
  label: string;
  fact?: UserOperationsFact | null;
  empty: string;
}) {
  return (
    <div className="min-w-0 px-5 py-4 sm:px-6">
      <dt className="text-xs font-bold text-slate-500">{label}</dt>
      <dd className="text-break mt-1 text-sm font-semibold leading-6 text-slate-900">{fact?.value || empty}</dd>
      {fact?.occurred_at && <p className="mt-1 text-xs text-slate-500">{formatDateTime(fact.occurred_at)}</p>}
    </div>
  );
}

function PersonalPortraitPanel({
  profile,
  stateProfile,
}: {
  profile?: UserProfile | null;
  stateProfile?: UserStateProfile | null;
}) {
  const profileJson = recordValue(profile?.profile_json);
  const overview = recordValue(profileJson.overview);
  const needAnalysis = recordValue(profileJson.need_analysis);
  const domains = recordValue(recordValue(stateProfile?.state_json).domains);
  const goalState = recordValue(recordValue(recordValue(domains.goals).current).value);
  const riskState = recordValue(recordValue(recordValue(domains.risk_profile).current).value);
  const preferences = memoryItems(recordValue(domains.personal_preferences), 6);
  const habits = memoryItems(recordValue(domains.lifestyle_habits), 6);
  const summary = text(profile?.summary) || text(profileJson.summary);
  const serviceTraits = [
    ["真实诉求", formatOptionalValue(goalState.main_needs)],
    ["沟通与激励", preferences.join("；")],
    ["生活习惯与约束", habits.join("；")],
    ["坚持风险", formatOptionalValue(riskState.adherence_worry)],
    ["压力下的反应", formatOptionalValue(riskState.pressure_reaction)],
  ].filter((item): item is [string, string] => Boolean(item[1]));
  const serviceBaseline = [
    ["28 天目标", text(profileJson.day28_goal) || formatOptionalValue(goalState.day28_goal)],
    ["入营核心问题", text(profileJson.main_problem) || formatOptionalValue(goalState.main_problem)],
    ["初始优先需求", text(needAnalysis.priority_need)],
  ].filter((item): item is [string, string] => Boolean(item[1]));
  const bodyFacts = [
    ["性别 / 年龄", text(overview.gender_age)],
    ["身高 / 体重", text(overview.height_weight)],
    ["BMI", text(overview.bmi)],
    ["腰围", text(overview.waist) || text(overview.waist_bodyfat)],
    ["减重带", text(overview.weight_loss_band) || text(profileJson.weight_loss_band)],
  ].filter((item): item is [string, string] => Boolean(item[1]));
  const portraitUpdatedAt = latestTimestamp(profile?.created_at, stateProfile?.updated_at);

  return (
    <section className="panel overflow-hidden" aria-labelledby="portrait-heading">
      <div className="panel-header-clean flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="portrait-heading" className="panel-title">个人画像</h2>
          <p className="panel-subtitle">建档底稿回答“这个人是谁”，动态事实回答“服务中发生了什么”。</p>
        </div>
        {portraitUpdatedAt && <span className="shrink-0 text-xs leading-5 text-slate-600">更新于 {formatDateTime(portraitUpdatedAt)}</span>}
      </div>

      {summary && (
        <div className="border-b border-slate-200 px-5 py-4 sm:px-6">
          <div className="text-xs font-bold text-slate-500">入营阶段判断</div>
          <p className="text-break mt-1.5 max-w-[75ch] text-sm font-semibold leading-6 text-slate-950">{summary}</p>
          {profile?.created_at && (
            <p className="mt-1 text-xs text-slate-500">建档于 {formatDateTime(profile.created_at)}{profile.generated_by_model ? ` · ${profile.generated_by_model}` : ""}</p>
          )}
        </div>
      )}

      <div className="grid lg:grid-cols-[minmax(0,1.05fr)_minmax(18rem,0.95fr)]">
        <div className="min-w-0 px-5 py-5 sm:px-6">
          <PortraitSection title="这次服务的基线">
            {serviceBaseline.length > 0 ? (
              <dl className="divide-y divide-slate-100">
                {serviceBaseline.map(([label, value]) => <PortraitRow key={label} label={label} value={value} />)}
              </dl>
            ) : (
              <InlinePortraitEmpty>暂未形成明确的服务目标与优先需求。</InlinePortraitEmpty>
            )}
          </PortraitSection>
        </div>

        <div className="min-w-0 border-t border-slate-200 bg-slate-50/70 px-5 py-5 sm:px-6 lg:border-l lg:border-t-0">
          <PortraitSection title="服务这个人时要记住">
            {serviceTraits.length > 0 ? (
              <dl className="space-y-3">
                {serviceTraits.map(([label, value]) => (
                  <div key={label} className="min-w-0">
                    <dt className="text-xs font-bold text-slate-500">{label}</dt>
                    <dd className="text-break mt-0.5 text-sm font-medium leading-6 text-slate-800">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <InlinePortraitEmpty>尚未沉淀稳定偏好、生活约束或坚持风险。</InlinePortraitEmpty>
            )}
          </PortraitSection>
        </div>
      </div>

      {bodyFacts.length > 0 && (
        <div className="border-t border-slate-200 px-5 py-4 sm:px-6">
          <div className="mb-3 text-xs font-bold text-slate-500">身体与建档基线</div>
          <dl className="flex flex-wrap gap-x-7 gap-y-3">
            {bodyFacts.map(([label, value]) => (
              <div key={label} className="min-w-[7rem]">
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd className="text-break mt-0.5 text-sm font-bold text-slate-900">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {stateProfile && <StateEvidenceView state={stateProfile.state_json} updatedAt={stateProfile.updated_at} />}
    </section>
  );
}

function PortraitSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-2 text-sm font-bold text-slate-950">{title}</h3>{children}</section>;
}

function PortraitRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 py-3 first:pt-0 last:pb-0 sm:grid-cols-[7rem_1fr] sm:gap-4">
      <dt className="text-xs font-bold text-slate-500">{label}</dt>
      <dd className="text-break text-sm font-medium leading-6 text-slate-900">{value}</dd>
    </div>
  );
}

function InlinePortraitEmpty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm leading-6 text-slate-600">{children}</p>;
}

function StateEvidenceView({ state, updatedAt }: { state: Record<string, unknown>; updatedAt: string }) {
  const domains = recordValue(state.domains);
  const detailKeys = [
    "service_state",
    "personal_preferences",
    "lifestyle_habits",
    "staff_guidance",
    "goals",
    "need_analysis",
    "diet_structure",
    "rhythm_structure",
    "product_usage",
    "body_feedback",
    "risk_profile",
    "outcome_changes",
  ];
  const detailDomains = detailKeys
    .map((key) => [key, recordValue(domains[key])] as const)
    .filter(([, domain]) => {
      const current = recordValue(domain.current);
      const hasCurrentUpdate = Object.keys(current).length > 0 && text(current.source) !== "intake";
      const hasDynamicHistory = Array.isArray(domain.history)
        && domain.history.some((item) => text(recordValue(item).source) !== "intake");
      return hasCurrentUpdate || hasDynamicHistory;
    });

  return (
    <div className="border-t border-slate-200">
      {detailDomains.length > 0 ? (
        <details className="group">
          <summary className="flex min-h-12 cursor-pointer list-none flex-col justify-center gap-0.5 px-5 py-3 text-sm font-semibold text-cyan-800 marker:hidden sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <span className="group-open:hidden">查看动态事实与原话依据</span>
            <span className="hidden group-open:inline">收起动态事实与原话依据</span>
            <span className="text-xs font-normal text-slate-500">{detailDomains.length} 类 · 更新于 {formatDateTime(updatedAt)}</span>
          </summary>
          <div className="divide-y divide-slate-200 border-t border-slate-200 px-5 sm:px-6">
            {detailDomains.map(([key, domain]) => <StateDomainRow key={key} domain={domain} />)}
          </div>
        </details>
      ) : (
        <p className="px-5 py-3 text-sm text-slate-600 sm:px-6">暂无聊天或打卡形成的动态画像事实。</p>
      )}
    </div>
  );
}

function StateDomainRow({ domain }: { domain: Record<string, unknown> }) {
  const current = recordValue(domain.current);
  const currentIsDynamic = text(current.source) !== "intake";
  const currentValue = currentIsDynamic ? recordValue(current.value) : {};
  const history = Array.isArray(domain.history)
    ? domain.history.map(recordValue).filter((item) => text(item.source) !== "intake").slice(-4).reverse()
    : [];

  return (
    <div className="py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-bold text-slate-900">{text(domain.label) || "状态"}</h4>
        {currentIsDynamic && text(current.updated_at) && <span className="text-xs text-slate-500">{formatDateTime(text(current.updated_at))}</span>}
      </div>
      {Object.keys(currentValue).length > 0 && (
        <dl className="mt-2 grid gap-x-6 gap-y-2 text-sm leading-6 md:grid-cols-2">
          {Object.entries(currentValue).slice(0, 8).map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs font-semibold text-slate-500">{stateFieldLabel(key)}</dt>
              <dd className="text-break text-slate-800">{formatStateValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {history.length > 0 && (
        <details className="mt-2">
          <summary className="inline-flex min-h-10 cursor-pointer items-center text-xs font-semibold text-cyan-700">查看最近证据</summary>
          <div className="mt-1 space-y-2 border-t border-slate-100 pt-2">
            {history.map((item, index) => (
              <div key={`${text(item.evidence)}-${index}`} className="grid gap-1 text-xs leading-5 sm:grid-cols-[8rem_1fr]">
                <span className="text-slate-500">{formatDateTime(text(item.occurred_at))}</span>
                <span className="text-break text-slate-700">
                  {text(item.field_key) && <strong className="font-semibold text-slate-800">{stateFieldLabel(text(item.field_key))}：</strong>}
                  {text(item.evidence) || formatStateValue(item.value)}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

const CUSTOMER_TYPE_STYLE: Record<string, string> = {
  疾病管理型: "border-rose-200 bg-rose-50 text-rose-700",
  减重攻坚型: "border-amber-200 bg-amber-50 text-amber-800",
  健康投资型: "border-emerald-200 bg-emerald-50 text-emerald-700",
};

function CustomerTypeBadge({ type }: { type: string }) {
  return <span className={`status-pill ${CUSTOMER_TYPE_STYLE[type] ?? "pill-neutral"}`}>{type}</span>;
}

function attentionTone(code?: string) {
  if (code === "guarded" || code === "action_required") {
    return { pill: "pill-risk", surface: "bg-rose-50/60", icon: "text-rose-700", Icon: AlertTriangle };
  }
  if (code === "manual" || code === "watch") {
    return { pill: "pill-warn", surface: "bg-amber-50/50", icon: "text-amber-700", Icon: Clock3 };
  }
  if (code === "closed") {
    return { pill: "pill-neutral", surface: "bg-slate-50", icon: "text-slate-600", Icon: CheckCircle2 };
  }
  return { pill: "pill-ok", surface: "bg-emerald-50/40", icon: "text-emerald-700", Icon: ShieldCheck };
}

function serviceStageText(serviceCase: User["service_cases"][number]) {
  const labels: Record<string, string> = {
    created: "未建档",
    questionnaire_submitted: "已提交问卷",
    generating: "文件生成中",
    documents_generated: "文件已生成",
    reviewing: "待审核",
    approved: "已批准",
    in_service: "服务中",
    completed: "已毕业",
    refunded: "已退款",
  };
  const status = labels[serviceCase.status] || serviceCase.status;
  if (["completed", "refunded"].includes(serviceCase.status)) return `${status} · 服务已结束`;
  if (!["approved", "in_service"].includes(serviceCase.status)) return status;
  const start = serviceCase.service_plan_start_date || serviceCase.started_at;
  if (!start) return status;
  const days = Math.floor((Date.now() - new Date(start).getTime()) / 86400000) + 1;
  return days >= 1 ? `${status} · 第 ${days} 天` : `${status} · 计划未开始`;
}

function currentDomainItems(domain: Record<string, unknown>, limit: number) {
  const value = recordValue(recordValue(domain.current).value);
  const items: Array<{ key: string; value: string; occurredAt: string }> = [];
  for (const [key, raw] of Object.entries(value)) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const item of values) {
      const fact = recordValue(item);
      if (text(fact.status) === "resolved") continue;
      const formatted = formatStateValue(item);
      if (formatted && formatted !== "未记录") {
        items.push({ key, value: formatted, occurredAt: text(fact.occurred_at) || text(fact.last_confirmed_at) });
      }
    }
  }
  return items
    .sort((left, right) => Date.parse(right.occurredAt || "1970-01-01") - Date.parse(left.occurredAt || "1970-01-01"))
    .slice(0, limit);
}

function memoryItems(domain: Record<string, unknown>, limit: number) {
  const value = recordValue(recordValue(domain.current).value);
  return Object.entries(value)
    .flatMap(([key, raw]) => {
      const facts = Array.isArray(raw) ? raw : [raw];
      return facts.map((fact) => {
        const formatted = formatStateValue(fact);
        return formatted && formatted !== "未记录" ? `${stateFieldLabel(key)}：${formatted}` : "";
      });
    })
    .filter(Boolean)
    .slice(-limit);
}

function stateFieldLabel(key: string) {
  const labels: Record<string, string> = {
    main_needs: "服务需求",
    day28_expectation: "28 天期待",
    day28_goal: "28 天目标",
    main_problem: "核心问题",
    surface_needs: "表层需求",
    deep_needs: "深层需求",
    priority_need: "优先需求",
    next_actions: "下一步动作",
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
    hydration_habit: "饮水习惯",
    schedule_constraint: "时间约束",
    work_routine: "工作节奏",
    exercise_habit: "运动习惯",
    sleep_habit: "睡眠习惯",
    social_context: "社交 / 家庭场景",
    current_obstacle: "当前阻塞",
    execution_status: "执行状态",
    negative_change: "负向变化",
    discomfort: "身体不适",
    mood_state: "当前情绪",
    willingness: "当前意愿",
    unresolved_question: "待解决问题",
    user_commitment: "用户承诺",
    health_concern: "健康关注",
    user_belief: "用户个人认知",
    diet_pattern: "饮食结构",
    profile_state: "状态判断",
    product_schedule: "产品执行",
    energy_change: "精神变化",
    sleep_change: "睡眠变化",
    bowel_change: "排便变化",
    weight_change: "体重变化",
    waist_or_body_change: "腰围 / 体感",
    weight_result: "体重结果",
    waist_result: "腰围结果",
    challenge_goal_result: "目标结果",
  };
  if (key.startsWith("staff_guidance_") || key.startsWith("staff_correction_")) return "班主任确认安排";
  return labels[key] ?? key;
}

function formatStateValue(value: unknown): string {
  if (value === null || value === undefined) return "未记录";
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  if (Array.isArray(value)) return value.map(formatStateValue).filter((item) => item && item !== "未记录").join("、");
  const record = recordValue(value);
  return text(record.value) || Object.values(record).map(formatStateValue).filter((item) => item && item !== "未记录").join("；") || "未记录";
}

function formatOptionalValue(value: unknown) {
  const formatted = formatStateValue(value);
  return formatted === "未记录" ? "" : formatted;
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function latestTimestamp(...values: Array<string | null | undefined>) {
  return values.filter((value): value is string => Boolean(value)).sort((left, right) => Date.parse(right) - Date.parse(left))[0];
}

function formatDateTime(value?: string | null) {
  return value ? formatChinaDateTime(value) : "-";
}
