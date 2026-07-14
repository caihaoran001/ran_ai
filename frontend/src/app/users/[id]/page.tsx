import Link from "next/link";
import { api, Checkin, Message, User, UserDocument, UserStateProfile } from "@/lib/api";
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
  // 四个请求只依赖 serviceCase.id、彼此无依赖，并行发起（原先串行 await 会白白叠加往返时延）。
  const [checkins, messages, documents, aiContextPreview] = serviceCase
    ? await Promise.all([
        api<Checkin[]>(`/service-cases/${serviceCase.id}/checkins`).catch(() => []),
        api<Message[]>(`/service-cases/${serviceCase.id}/messages`).catch(() => []),
        api<UserDocument[]>(`/service-cases/${serviceCase.id}/documents`).catch(() => []),
        api<{ context: string }>(`/service-cases/${serviceCase.id}/ai-context-preview`).catch(() => null),
      ])
    : [[] as Checkin[], [] as Message[], [] as UserDocument[], null];
  const latestParsed = checkins[0]?.parsed_json;
  const uniqueCheckinDays = new Set(checkins.map(c => c.checkin_date).filter(Boolean)).size;
  const energyDocument = documents.find((item) => item.document_type === "energy_restart_prescription");
  const executionDocument = documents.find((item) => item.document_type === "day28_execution_standard");
  const overview = recordValue(profile?.profile_json?.overview);
  const customerType = text((profile?.profile_json as Record<string, unknown> | undefined)?.customer_type);

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div className="min-w-0">
            <h1 className="page-heading">{user.name}</h1>
            <p className="text-break page-subtitle">{user.phone ?? "无手机号"} · {user.wecom_user_id ?? "无聊天用户 ID"}</p>
            {user.deleted_at && (
              <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs font-bold text-red-700">
                已删除：{formatDateTime(user.deleted_at)}
              </p>
            )}
          </div>
          {customerType ? (
            <CustomerTypeBadge type={customerType} />
          ) : (
            <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">用户档案</span>
          )}
        </div>
      </div>

      {serviceCase && (
        <OperationsSummary
          serviceCase={serviceCase}
          stateProfile={stateProfile || undefined}
          checkins={checkins}
          uniqueCheckinDays={uniqueCheckinDays}
        />
      )}

      {profile ? (
        <CollapsibleSection title="建档底稿" subtitle="入营问卷生成的基础判断；服务期内不再重复改写">
          <BaselineProfile profile={profile.profile_json} overview={overview} />
        </CollapsibleSection>
      ) : (
        <p className="empty-state">尚未生成建档底稿。用户提交入营问卷后，系统会自动生成交付文件。</p>
      )}

      {stateProfile && (
        <CollapsibleSection title="当前用户状态" subtitle="证据驱动的动态状态，默认收起">
          <LongTermMemoryView state={stateProfile.state_json} />
          <StateProfileView state={stateProfile.state_json} updatedAt={stateProfile.updated_at} />
        </CollapsibleSection>
      )}

      {serviceCase && (
        <CollapsibleSection title="运营明细" subtitle="方案 · 打卡 · 交付文件 · 聊天 · 服务单">
          <div className="space-y-5">
            <div>
              <SubHeading>正在进行的方案</SubHeading>
              <Link className="text-break block rounded-md border border-slate-200 bg-slate-50/60 px-3 py-2 text-sm font-bold text-slate-900 hover:border-cyan-300 hover:bg-white" href={`/cases/${serviceCase.id}`}>
                {serviceCase.case_no} · {statusLabel(serviceCase.status)}
              </Link>
              <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
                <Info label="人工介入" value={serviceCase.needs_human_intervention ? reasonText(serviceCase.intervention_reasons) : "暂无"} />
                <Info label="风险原因" value={serviceCase.risk_level === "high" ? reasonText(serviceCase.risk_flags) : "暂无"} />
                <Info label="AI自动回复" value={serviceCase.ai_auto_reply_enabled === false ? "关闭" : "开启"} />
                <Info label="续购意向" value={intentText(serviceCase.renewal_intent_flags)} />
                <Info label="退款/退出意向" value={intentText(serviceCase.refund_intent_flags)} />
              </div>
            </div>

            <div>
              <SubHeading>打卡数据</SubHeading>
              <div className="grid gap-3 text-sm md:grid-cols-3">
                <Info label="体重变化" value={weightTrend(checkins)} />
                <Info label="睡眠变化" value={latestParsed?.sleep || "暂无数据"} />
                <Info label="情绪变化" value={latestParsed?.mood || "暂无数据"} />
                <Info label="饮水" value={latestParsed?.water || "暂无数据"} />
                <Info label="运动" value={latestParsed?.exercise || "暂无数据"} />
                <Info label="打卡天数" value={`${uniqueCheckinDays} 天`} />
              </div>
            </div>

            {(energyDocument || executionDocument) && (
              <div>
                <SubHeading>交付文件摘要</SubHeading>
                <DocumentHighlights energyDocument={energyDocument} executionDocument={executionDocument} />
              </div>
            )}

            <div>
              <SubHeading>历史聊天摘要（{chatSummary(messages)}）</SubHeading>
              <div className="max-h-48 space-y-2 overflow-y-auto pr-2 text-sm">
                {messages.slice(-5).map((message) => (
                  <div key={message.id} className="rounded-md border border-slate-200 bg-slate-50/60 p-2.5">
                    <span className="font-bold text-slate-600">{
                      message.sender_type === "bot" ? "燃人.AI"
                        : message.sender_type === "staff" ? (message.display_sender_name || "班主任")
                        : message.direction === "outbound" ? "运营"
                        : (message.display_sender_name || "客户")
                    }：</span>
                    <span className="text-break whitespace-pre-wrap">{message.content}</span>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <SubHeading>全部服务单</SubHeading>
              <div className="grid max-h-48 gap-2 overflow-y-auto pr-2 text-sm">
                {user.service_cases.map((item) => (
                  <Link key={item.id} className="text-break rounded-md border border-slate-200 bg-slate-50/60 px-3 py-2 font-bold text-slate-900 hover:border-cyan-300 hover:bg-white" href={`/cases/${item.id}`}>
                    {item.case_no} · {item.group_id} · {statusLabel(item.status)}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </CollapsibleSection>
      )}

      {profile && (
        <CollapsibleSection title="调试信息" subtitle="AI 提示词上下文预览，日常运营可忽略">
          <div className="text-break max-w-full overflow-x-auto whitespace-pre-wrap font-mono text-xs leading-6 text-slate-600 select-all">
            {aiContextPreview?.context || "暂无 AI 上下文。"}
          </div>
        </CollapsibleSection>
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
  const style = CUSTOMER_TYPE_STYLE[type] ?? "border-slate-200 bg-slate-50 text-slate-700";
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-bold ${style}`}>
      {type}
    </span>
  );
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">{children}</div>;
}

function CollapsibleSection({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="panel group overflow-hidden">
      <summary className="panel-header-clean flex cursor-pointer select-none items-center justify-between">
        <div>
          <h2 className="panel-title">{title}</h2>
          {subtitle && <p className="panel-subtitle">{subtitle}</p>}
        </div>
        <span className="text-xs text-slate-400 group-open:hidden">展开</span>
        <span className="hidden text-xs text-slate-400 group-open:inline">收起</span>
      </summary>
      <div className="panel-body">{children}</div>
    </details>
  );
}

function Info({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className={`rounded-lg border p-3 ${muted ? "border-slate-200 bg-slate-100 text-slate-500" : "border-slate-200 bg-slate-50/70"}`}>
      <div className="text-xs font-bold text-slate-600">{label}</div>
      <div className={`text-break mt-1 font-bold ${muted ? "text-slate-500" : "text-slate-950"}`}>{value}</div>
    </div>
  );
}

function OperationsSummary({
  serviceCase,
  stateProfile,
  checkins,
  uniqueCheckinDays,
}: {
  serviceCase: User["service_cases"][number];
  stateProfile?: UserStateProfile;
  checkins: Checkin[];
  uniqueCheckinDays: number;
}) {
  const domains = recordValue(stateProfile?.state_json?.domains);
  const focus = currentActionFocus(domains);
  const attentionText = serviceCase.needs_human_intervention
    ? reasonText(serviceCase.intervention_reasons) || "存在待关注事项"
    : serviceCase.risk_level === "high"
      ? reasonText(serviceCase.risk_flags)
      : "";
  const latestChange = latestStateChange(domains) || latestCheckinSignal(checkins);
  const latestChangeAt = latestStateChangeAt(domains) || stateProfile?.updated_at;
  const nextAction = text(focus.action) || "等待新的有效反馈后由档案官确定下一步。";

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="panel-title">当前运营状态</h2>
            <p className="panel-subtitle">档案官基于有效聊天、打卡和班主任纠正持续维护。</p>
          </div>
          <span className="status-pill pill-info">
            {serviceStageText(serviceCase)}
          </span>
        </div>
      </div>
      <dl className="grid divide-y divide-slate-200 text-sm sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <OperationItem label="当前要抓" value={text(focus.label) || "暂无明确重点"} />
        <OperationItem label="下一步" value={nextAction} emphasis />
        <OperationItem
          label="最近变化"
          value={latestChange || "暂无新变化"}
          meta={latestChangeAt ? `更新于 ${formatDateTime(latestChangeAt)}` : undefined}
        />
        <OperationItem label="打卡" value={`已打卡 ${uniqueCheckinDays} 天`} />
        {attentionText && <OperationItem label="需要关注" value={attentionText} tone="risk" />}
      </dl>
    </section>
  );
}

function OperationItem({
  label,
  value,
  meta,
  emphasis = false,
  tone = "default",
}: {
  label: string;
  value: string;
  meta?: string;
  emphasis?: boolean;
  tone?: "default" | "risk";
}) {
  return (
    <div className={`min-w-0 px-5 py-4 ${tone === "risk" ? "bg-rose-50/70 sm:col-span-2" : ""}`}>
      <dt className={`text-xs font-bold ${tone === "risk" ? "text-rose-700" : "text-slate-500"}`}>{label}</dt>
      <dd className={`text-break mt-1.5 max-w-[68ch] text-sm leading-6 ${emphasis ? "font-bold text-cyan-900" : tone === "risk" ? "font-semibold text-rose-900" : "font-medium text-slate-900"}`}>{value}</dd>
      {meta && <div className="mt-1 text-xs text-slate-500">{meta}</div>}
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

function latestStateChangeAt(domains: Record<string, unknown>) {
  for (const key of ["outcome_changes", "body_feedback", "product_usage", "risk_profile"]) {
    const domain = recordValue(domains[key]);
    const current = recordValue(domain.current);
    if (text(current.updated_at)) return text(current.updated_at);
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

function BaselineProfile({
  profile,
  overview,
}: {
  profile: Record<string, unknown>;
  overview: Record<string, unknown>;
}) {
  const needAnalysis = recordValue(profile.need_analysis);
  const tags = Array.isArray(profile.tags) ? profile.tags.map(String).filter(Boolean) : [];

  return (
    <div className="space-y-5 text-sm">
      <dl className="grid gap-x-8 gap-y-4 md:grid-cols-2">
        <BaselineItem label="28 天目标" value={text(profile.day28_goal) || "暂无数据"} />
        <BaselineItem label="入营核心问题" value={text(profile.main_problem) || "暂无数据"} />
        <BaselineItem label="初始优先需求" value={text(needAnalysis.priority_need) || "暂无数据"} />
        <BaselineItem label="减重带" value={profileText(profile, "weight_loss_band") || "暂无数据"} />
      </dl>
      <div className="border-t border-slate-200 pt-4">
        <div className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <BaselineItem label="性别/年龄" value={text(overview.gender_age) || "暂无数据"} compact />
          <BaselineItem label="身高/体重" value={text(overview.height_weight) || "暂无数据"} compact />
          <BaselineItem label="BMI" value={text(overview.bmi) || "暂无数据"} compact />
          <BaselineItem label="腰围" value={text(overview.waist) || text(overview.waist_bodyfat) || "暂无数据"} compact />
        </div>
      </div>
      {tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
          <span className="text-xs font-bold text-slate-500">建档标签</span>
          {tags.map((tag) => <span key={tag} className="status-pill pill-neutral">{tag}</span>)}
        </div>
      )}
    </div>
  );
}

function BaselineItem({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-bold text-slate-500">{label}</dt>
      <dd className={`text-break mt-1 ${compact ? "font-medium text-slate-800" : "max-w-[68ch] leading-6 text-slate-900"}`}>{value}</dd>
    </div>
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
      <p className="text-xs text-slate-500">
        默认只展示运营接话最常用的信息；完整字段和证据链可展开查看。
        最近更新：{formatDateTime(updatedAt)}
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {primaryDomains.map(([key, domain]) => (
          <StateDomainCard key={key} domain={domain} />
        ))}
      </div>
      {secondaryDomains.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-slate-50">
          <summary className="flex cursor-pointer items-center justify-between px-4 py-3 text-sm font-medium text-slate-700">
            <span>查看完整状态字段</span>
            <span className="text-xs font-normal text-slate-400">{secondaryDomains.length} 个补充字段</span>
          </summary>
          <div className="grid gap-3 border-t border-slate-200 p-3 lg:grid-cols-2">
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
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-slate-500">{label}</div>
        {text(current.updated_at) && <div className="text-[11px] text-slate-400">{formatDateTime(text(current.updated_at))}</div>}
      </div>
      {Object.keys(currentValue).length > 0 ? (
        <div className="mt-2 space-y-1">
          {Object.entries(currentValue).slice(0, 5).map(([key, value]) => (
            <div key={key} className="leading-6">
              <span className="text-slate-500">{stateFieldLabel(key)}：</span>
              <span className="text-break">{formatStateValue(value)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 text-slate-500">暂无当前状态</div>
      )}
      {evidence && <p className="text-break mt-2 rounded bg-white px-2 py-1 text-xs leading-5 text-slate-500">依据：{evidence}</p>}
      {history.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-blue-600">查看证据链</summary>
          <div className="mt-2 space-y-2">
            {history.map((item, index) => (
              <div key={`${text(item.evidence)}-${index}`} className="rounded border border-slate-100 bg-white p-2 text-xs leading-5">
                <div className="text-slate-400">{formatDateTime(text(item.occurred_at))}</div>
                <div className="text-break mt-1 text-slate-600">{text(item.evidence) || formatStateValue(item.value)}</div>
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
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-slate-500">当前行动重点</div>
        {text(current.updated_at) && <div className="text-[11px] text-slate-400">{formatDateTime(text(current.updated_at))}</div>}
      </div>
      {text(focus.label) ? (
        <div className="mt-2 space-y-1 leading-6">
          <div className="text-break"><span className="text-slate-500">重点：</span>{text(focus.label)}</div>
          <div className="text-break"><span className="text-slate-500">动作：</span>{text(focus.action) || "暂无动作"}</div>
          <div><span className="text-slate-500">分数：</span>{String(focus.score ?? "暂无")}</div>
          {text(focus.soft_lock_until) && <div><span className="text-slate-500">稳定期至：</span>{formatDateTime(text(focus.soft_lock_until))}</div>}
        </div>
      ) : (
        <div className="mt-2 text-slate-500">暂无当前行动重点</div>
      )}
      {sortedCandidates.length > 0 && (
        <div className="mt-3 rounded bg-white px-2 py-2 text-xs leading-5 text-slate-500">
          <div className="font-medium text-slate-600">候选池</div>
          {sortedCandidates.map((item) => (
          <div key={text(item.key) || text(item.label)} className="text-break">{text(item.label)}：{String(item.score ?? "0")}</div>
          ))}
        </div>
      )}
      {evidence && <p className="text-break mt-2 rounded bg-white px-2 py-1 text-xs leading-5 text-slate-500">依据：{evidence}</p>}
      {history.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-blue-600">查看变更记录</summary>
          <div className="mt-2 space-y-2">
            {history.map((item, index) => (
              <div key={`${text(item.evidence)}-${index}`} className="rounded border border-slate-100 bg-white p-2 text-xs leading-5">
                <div className="text-slate-400">{formatDateTime(text(item.occurred_at))}</div>
                <div className="text-break mt-1 text-slate-600">{text(item.evidence) || formatStateValue(item.value)}</div>
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
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs font-medium text-slate-500">{title}</div>
      <div className="text-break mt-1 whitespace-pre-wrap">{value}</div>
    </div>
  );
}

function SummaryListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs font-medium text-slate-500">{title}</div>
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
