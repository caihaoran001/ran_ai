import Link from "next/link";
import type { ComponentType } from "react";
import { api, FRONTEND_BASE, ServiceCase, ServiceCaseStats, UserDocument, Checkin, Message, OutcomeBundle } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";
import { CaseActions } from "./case-actions";
import { CaseRiskReset } from "./case-risk-reset";
import { CaseRefundControls } from "./case-refund-controls";
import { CaseGeneratingPoller } from "./case-generating-poller";
import { ChatMessages } from "./chat-messages";
import { QuestionnaireSection } from "./questionnaire-section";
import { RetryGenerateDocuments } from "./retry-generate-documents";
import { CaseAutomationToggles } from "./case-automation-toggles";
import { StageReportActions } from "./stage-report-actions";
import { OutcomeSection } from "./outcome-section";
import {
  AlertTriangle,
  BarChart3,
  Brain,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  HeartHandshake,
  Link as LinkIcon,
  LoaderCircle,
  MessageCircle,
  PencilLine,
  RefreshCcw,
  Timer,
  UserRound,
  Zap,
} from "lucide-react";

const PERSISTENT_HIGH_RISK_CODES = new Set([
  "pregnancy",
  "lactation",
  "chronic_disease",
  "long_term_medication",
  "hormone_medication",
  "liver_kidney",
  "stone",
  "severe_test_abnormality",
  "depression",
]);

type Questionnaire = {
  fields: {
    key: string;
    label: string;
    title?: string;
    subtitle?: string;
    type: string;
  }[];
  latest_submission?: {
    answers_json: Record<string, unknown>;
    created_at: string;
  } | null;
};

export default async function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const serviceCase = await api<ServiceCase>(`/service-cases/${id}`);
  const [
    documents,
    documentHistoryResult,
    checkins,
    stats,
    messages,
    outcomes,
    questionnaire,
  ] = await Promise.all([
    api<UserDocument[]>(`/service-cases/${id}/documents`).catch(() => []),
    api<UserDocument[]>(`/service-cases/${id}/documents?include_history=true`).catch(() => null),
    api<Checkin[]>(`/service-cases/${id}/checkins`).catch(() => []),
    api<ServiceCaseStats>(`/service-cases/${id}/stats`).catch(() => null),
    api<Message[]>(`/service-cases/${id}/messages`).catch(() => []),
    api<OutcomeBundle>(`/service-cases/${id}/outcomes`).catch(() => null),
    api<Questionnaire>(`/questionnaires/${serviceCase.questionnaire_token}`).catch(() => null),
  ]);
  const documentHistory = documentHistoryResult ?? documents;
  const questionnaireUrl = `${FRONTEND_BASE.replace(/\/$/, "")}/q/${serviceCase.questionnaire_token}`;
  const isGenerating = serviceCase.status === "generating";
  const serviceGraduationDate = getServiceGraduationDate(serviceCase.started_at, serviceCase.service_plan_start_date);
  const serviceWindowEnded = Boolean(serviceGraduationDate && chinaDateKey(new Date()) > serviceGraduationDate);
  const refunded = serviceCase.status === "refunded";
  const highRiskFlag = persistentHighRiskFlag(serviceCase.risk_flags);
  const generationError =
    serviceCase.status === "questionnaire_submitted" && documents.length === 0
      ? extractGenerationError(serviceCase.notes)
      : null;

  return (
    <div className="page-stack">
      {isGenerating && <CaseGeneratingPoller />}
      <div className="page-header">
        <div className="page-header-row">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="page-heading">{serviceCase.case_no}</h1>
              <StatusBadge status={serviceCase.status} />
            </div>
            <p className="text-break mt-2 text-sm text-slate-600">
              {serviceCase.user ? (
                <Link
                  href={`/users/${serviceCase.user.id}`}
                  className="inline-flex items-center gap-1 font-bold text-cyan-700 hover:text-cyan-900 hover:underline"
                >
                  <UserRound className="h-4 w-4" />
                  {serviceCase.user.name}（查看完整用户画像与打卡历史）
                </Link>
              ) : (
                "待填写问卷用户"
              )}
               · {serviceCase.group_id}
            </p>
            {serviceCase.deleted_at && (
              <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-3 py-1 text-xs font-bold text-red-700">
                已删除：{formatDateTime(serviceCase.deleted_at)}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {serviceCase.risk_level === "high" && highRiskFlag && (
                <span className="status-pill border-rose-200 bg-rose-50 text-rose-700">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  高风险
                  <span className="opacity-80">· {riskLabel(highRiskFlag)}</span>
                </span>
              )}
              {serviceCase.risk_level === "high" && (
                <CaseRiskReset caseId={serviceCase.id} />
              )}
            </div>
          </div>
          {!serviceCase.deleted_at && (
            <div className="flex flex-wrap items-center gap-2 lg:justify-end">
              {/* 候选态的确认横幅单独整行展示；此处仅放常态「标记已退款」/已退款「恢复服务」的紧凑按钮 */}
              {!(serviceCase.refund_pending_confirmation && !refunded) && (
                <CaseRefundControls caseId={serviceCase.id} status={serviceCase.status} refundPending={false} />
              )}
              <CaseActions caseId={serviceCase.id} currentStartedAt={serviceCase.started_at} />
            </div>
          )}
        </div>
      </div>

      {!serviceCase.deleted_at && serviceCase.refund_pending_confirmation && !refunded && (
        <CaseRefundControls caseId={serviceCase.id} status={serviceCase.status} refundPending />
      )}

      {generationError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          <p className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="h-4 w-4" />
            文件自动生成失败
          </p>
          <p className="mt-1 break-words">{generationError}</p>
          {!serviceCase.deleted_at && (
            <RetryGenerateDocuments caseId={serviceCase.id} autoSend={serviceCase.auto_send_documents} />
          )}
        </div>
      )}

      {isGenerating && (
        <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-5 py-4 text-sm text-cyan-800">
          <p className="flex items-center gap-2 font-semibold">
            <LoaderCircle className="h-4 w-4 animate-spin" />
            文件生成中
          </p>
          <p className="mt-1">正在生成您的专属文件，预计 10-30 秒。页面会每 3 秒自动刷新一次，您现在离开页面也不会中断。</p>
        </div>
      )}

      {!serviceCase.deleted_at && (
        <CaseAutomationToggles
          serviceCaseId={serviceCase.id}
          aiAutoReplyEnabled={serviceCase.ai_auto_reply_enabled}
        />
      )}

      <div className="grid gap-4 xl:grid-cols-12">
        <InfoCard
          title="问卷链接"
          value={questionnaireUrl}
          icon={LinkIcon}
          className="xl:col-span-6"
          valueClassName="text-base sm:text-lg"
        />
        <InfoCard title="交付文件" value={`${documents.length} 份`} icon={FileText} className="xl:col-span-2" />
        <InfoCard title="打卡记录" value={`${checkins.length} 条`} icon={CheckCircle2} className="xl:col-span-2" />
        <InfoCard title="聊天记录" value={`${messages.length} 条`} icon={MessageCircle} className="xl:col-span-2" />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <InfoCard
          title="已服务时长"
          value={formatServiceDuration(
            serviceCase.started_at,
            serviceCase.service_plan_start_date,
            serviceCase.ended_at,
            serviceCase.status,
          )}
          icon={Timer}
        />
        <InfoCard title="内容生成耗时" value={formatGenerationDuration(serviceCase.questionnaire_to_llm_output_ms)} icon={Zap} />
        <InfoCard title="群最后活跃" value={formatDateTime(stats?.last_message_at)} icon={Clock3} />
        <InfoCard title="用户最后发言" value={formatDateTime(stats?.last_user_message_at)} icon={UserRound} />
        <InfoCard title="用户发言数" value={`${stats?.inbound_message_count ?? 0} 条`} icon={PencilLine} />
        <InfoCard title="日均发言" value={`${stats?.avg_daily_inbound_messages ?? 0} 条/天`} icon={BarChart3} />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <AICostInfoCard
          cost={stats?.ai_estimated_cost_yuan}
          deepseekCost={stats?.ai_deepseek_cost_yuan}
          visionCost={stats?.ai_vision_cost_yuan}
          tokens={stats?.ai_total_tokens}
          calls={stats?.ai_total_calls}
          className="xl:col-span-2"
        />
        <InfoCard title="续购意向" value={formatIntentFlags(serviceCase.renewal_intent_flags)} icon={HeartHandshake} />
        <InfoCard title="退款/退出意向" value={formatIntentFlags(serviceCase.refund_intent_flags)} icon={RefreshCcw} />
        {highRiskFlag && (
          <InfoCard
            title="持续健康风险依据"
            value={`${riskLabel(highRiskFlag)}：${highRiskFlag.evidence || highRiskFlag.reason}`}
            icon={AlertTriangle}
            className="border-rose-200 bg-rose-50/50"
            valueClassName="text-rose-900"
          />
        )}
      </div>

      <OutcomeSection bundle={outcomes} />

      <div className="grid gap-6 md:grid-cols-2">
        <QuestionnaireSection questionnaire={questionnaire} editUrl={`${questionnaireUrl}?edit=1`} />
        
        <div className="panel">
          <div className="panel-header">
            <h2 className="panel-title">交付文件</h2>
          </div>
          <div className="p-6">
            {documents.length > 0 ? (
              <div className="space-y-3">
                {documents.map((document) => (
                  <div key={document.id} className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4 transition-colors hover:border-cyan-300 hover:bg-white sm:flex-row sm:items-center sm:justify-between">
                    <Link className="text-break font-bold text-slate-950 hover:text-cyan-700" href={`/documents/${document.id}`}>
                      {document.title}
                    </Link>
                    <div className="flex flex-wrap items-center gap-3">
                      <StatusBadge status={document.status} />
                      <a
                        suppressHydrationWarning
                        className="admin-link text-sm"
                        href={`/api/backend/documents/${document.id}/download`}
                      >
                        下载
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <FileText className="mx-auto mb-2 h-9 w-9" />
                <p>还没有生成交付文件</p>
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2 className="panel-title">阶段报告</h2>
            <p className="panel-subtitle">生成后会进入文档审核，可编辑、下载或发送。</p>
          </div>
          <div className="p-6">
            <StageReportActions serviceCaseId={serviceCase.id} documents={documentHistory} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2 className="panel-title">最近打卡</h2>
          </div>
          <div className="p-6">
            {checkins.length > 0 ? (
              <div className="space-y-3">
                {checkins.slice(0, 5).map((checkin) => (
                  <div key={checkin.id} className="flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-emerald-100 bg-emerald-50 text-emerald-700">
                      <CheckCircle2 className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <StructuredCheckin checkin={checkin} />
                      <div className="mt-2 text-xs font-semibold text-slate-500">{formatDateTime(checkin.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <ClipboardList className="mx-auto mb-2 h-9 w-9" />
                <p>暂无打卡记录</p>
              </div>
            )}
          </div>
        </div>

        <ChatMessages messages={messages} userName={serviceCase.user?.name} />
      </div>
    </div>
  );
}

function StructuredCheckin({ checkin }: { checkin: Checkin }) {
  const parsed = checkin.parsed_json;
  if (!parsed) return <div className="text-break text-sm text-slate-900">{checkin.content}</div>;
  const product = parsed.product_schedule || {};
  const rows = [
    ["早C", product.morning_c],
    ["午B", product.noon_b],
    ["晚A", product.night_a],
    ["饮水", parsed.water],
    ["睡眠", parsed.sleep],
    ["情绪", parsed.mood],
    ["运动", parsed.exercise],
    ["体重", parsed.weight],
  ].filter(([, value]) => value);
  if (rows.length === 0) return <div className="text-break text-sm text-slate-900">{checkin.content}</div>;
  return (
    <div className="grid gap-2 text-sm text-slate-900 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="text-break rounded-md border border-slate-200 bg-white px-2.5 py-1.5">
          <span className="font-bold text-slate-600">{label}：</span>{value}
        </div>
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const statusConfig: Record<string, { label: string; className: string }> = {
    created: { label: "已创建", className: "border-slate-200 bg-slate-50 text-slate-700" },
    questionnaire_submitted: { label: "已提交问卷", className: "border-amber-200 bg-amber-50 text-amber-700" },
    generating: { label: "文件生成中", className: "border-cyan-200 bg-cyan-50 text-cyan-700" },
    documents_generated: { label: "已生成文档", className: "border-cyan-200 bg-cyan-50 text-cyan-700" },
    reviewing: { label: "审核中", className: "border-violet-200 bg-violet-50 text-violet-700" },
    approved: { label: "已批准", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    in_service: { label: "服务中", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    completed: { label: "已毕业", className: "border-slate-200 bg-slate-50 text-slate-600" },
    refunded: { label: "已退款", className: "border-rose-200 bg-rose-50 text-rose-700" },
    sent: { label: "已发送", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    draft: { label: "草稿", className: "border-slate-200 bg-slate-50 text-slate-600" },
    archived: { label: "已归档·未发送", className: "border-slate-200 bg-slate-100 text-slate-500" },
  };

  const config = statusConfig[status] || { label: status, className: "border-slate-200 bg-slate-50 text-slate-700" };

  if (status === "refunded") {
    return (
      <span className="status-pill border-rose-300 bg-rose-100 font-semibold text-rose-700 ring-1 ring-rose-300 ring-offset-1">
        <RefreshCcw className="h-3.5 w-3.5" />
        已退款
      </span>
    );
  }

  return (
    <span className={`status-pill ${config.className}`}>
      {config.label}
    </span>
  );
}

function InfoCard({
  title,
  value,
  icon,
  className = "",
  valueClassName = "",
}: {
  title: string;
  value: string;
  icon: ComponentType<{ className?: string }>;
  className?: string;
  valueClassName?: string;
}) {
  const Icon = icon;
  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-colors hover:border-cyan-200 hover:bg-slate-50/40 ${className}`}>
      <div className="flex items-center gap-2 text-sm font-bold text-slate-600">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-cyan-50 text-cyan-700 ring-1 ring-cyan-100">
          <Icon className="h-4 w-4" />
        </span>
        {title}
      </div>
      <div className={`text-break mt-3 text-lg font-black text-slate-950 ${valueClassName}`}>{value}</div>
    </div>
  );
}

function AICostInfoCard({
  cost,
  deepseekCost,
  visionCost,
  tokens,
  calls,
  className = "",
}: {
  cost?: number | null;
  deepseekCost?: number | null;
  visionCost?: number | null;
  tokens?: number | null;
  calls?: number | null;
  className?: string;
}) {
  const costValue = cost ?? 0;
  const deepseekCostValue = deepseekCost ?? 0;
  const visionCostValue = visionCost ?? 0;
  const tokenCount = tokens ?? 0;
  const callCount = calls ?? 0;
  return (
    <div className={`rounded-xl border border-cyan-200 bg-cyan-50/70 p-5 shadow-sm ${className}`}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-cyan-800">
            <Brain className="h-4 w-4" />
            AI调用成本
            <span className="rounded-full border border-cyan-200 bg-white/80 px-2 py-0.5 text-[11px] font-semibold text-cyan-700">
              估算
            </span>
          </div>
          <div className="mt-2 text-2xl font-bold tracking-tight text-slate-950">¥{costValue.toFixed(2)}</div>
          <p className="mt-1 text-xs text-slate-500">DeepSeek + 识图模型调用合计</p>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 text-xs sm:min-w-56">
          <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
            <div className="text-slate-400">DeepSeek</div>
            <div className="mt-1 font-semibold text-slate-800">¥{deepseekCostValue.toFixed(2)}</div>
          </div>
          <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
            <div className="text-slate-400">识图</div>
            <div className="mt-1 font-semibold text-slate-800">¥{visionCostValue.toFixed(2)}</div>
          </div>
          <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
            <div className="text-slate-400">Tokens</div>
            <div className="mt-1 font-semibold text-slate-800">{tokenCount.toLocaleString("zh-CN")}</div>
          </div>
          <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
            <div className="text-slate-400">调用</div>
            <div className="mt-1 font-semibold text-slate-800">{callCount.toLocaleString("zh-CN")} 次</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function formatDateTime(value?: string | null) {
  if (!value) return "暂无";
  return formatChinaDateTime(value, "暂无");
}

function formatServiceDuration(
  startedAt?: string | null,
  planStartDate?: string | null,
  endedAt?: string | null,
  status?: string | null,
) {
  const graduationDate = getServiceGraduationDate(startedAt, planStartDate);
  if (graduationDate && chinaDateKey(new Date()) > graduationDate) {
    return `已于 ${formatDateKey(graduationDate)} 毕业`;
  }
  if (status === "completed" && endedAt) {
    return `已于 ${formatChinaDateTime(endedAt, "已完成")} 完成`;
  }
  // 填了计划开始日期：按计划日算——未到则"待开始"，当天=第1天
  if (planStartDate) {
    const plan = new Date(`${planStartDate}T00:00:00`);
    const now = new Date();
    const todayMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (todayMidnight < plan) {
      return `待开始（计划 ${planStartDate} 起）`;
    }
    const days = Math.floor((todayMidnight.getTime() - plan.getTime()) / 86_400_000) + 1;
    return `第 ${days} 天`;
  }
  // 未填计划日：沿用文件发送时刻算时长（存量行为不变）
  if (!startedAt) return "未开始";
  const started = new Date(startedAt).getTime();
  const diffMs = Date.now() - started;
  if (!Number.isFinite(started) || diffMs < 0) return "未开始";
  const totalHours = Math.floor(diffMs / 3_600_000);
  const days = Math.floor(totalHours / 24);
  const hours = totalHours % 24;
  if (days <= 0) return `${hours}h`;
  return `${days}天零${hours}h`;
}

function getServiceGraduationDate(startedAt?: string | null, planStartDate?: string | null) {
  if (planStartDate) {
    return addDaysToDateKey(planStartDate, 27);
  }
  if (!startedAt) return null;
  return addDaysToDateKey(chinaDateKey(new Date(startedAt)), 28);
}

function chinaDateKey(date: Date) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function addDaysToDateKey(dateKey: string, days: number) {
  const [year, month, day] = dateKey.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

function formatDateKey(dateKey: string) {
  const [year, month, day] = dateKey.split("-");
  return `${year}/${month}/${day}`;
}

function formatGenerationDuration(durationMs?: number | null) {
  if (!durationMs || durationMs < 0) return "暂无";
  if (durationMs < 1000) return `${durationMs}ms`;
  const totalSeconds = durationMs / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(totalSeconds >= 10 ? 1 : 2)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}分${seconds.toFixed(1)}秒`;
}

function extractGenerationError(notes?: string | null) {
  if (!notes || !notes.includes("自动生成画像/交付文件失败")) return null;
  const lines = notes
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const matched = [...lines].reverse().find((line) => line.includes("自动生成画像/交付文件失败"));
  return matched?.replace(/^\[[^\]]+\]\s*/, "") ?? null;
}

function formatIntentFlags(flags?: Array<Record<string, string | boolean>> | null) {
  if (!flags?.length) return "暂无";
  const latest = flags[flags.length - 1];
  const evidence = typeof latest.evidence === "string" ? latest.evidence : "";
  return evidence || "已识别";
}

function riskLabel(flag: Record<string, string>) {
  const labels: Record<string, string> = {
    pregnancy: "孕期/怀孕相关",
    lactation: "哺乳期相关",
    chronic_disease: "慢病相关",
    long_term_medication: "长期用药相关",
    depression: "情绪危机/抑郁相关",
    hormone_medication: "激素类药物相关",
    liver_kidney: "肝肾异常相关",
    stone: "结石相关",
    severe_test_abnormality: "体检严重异常相关",
    severe_insomnia: "严重失眠相关",
    chest_pain: "胸痛/胸闷相关",
    palpitation: "心悸/心慌相关",
    allergy: "过敏相关",
    medical_claim: "医疗诊断/治疗承诺相关",
    other_risk: "其他风险",
    emotional_distress: "情绪危机/心理风险",
    risk_classifier_unavailable: "风控识别不可用",
    medication: "药物相关",
    physical_discomfort: "身体不适相关",
    complaint: "投诉/不满相关",
  };
  const label = labels[flag.code] || flag.code || "未知风险";
  return flag.keyword ? `${label}（命中：${flag.keyword}）` : label;
}

function persistentHighRiskFlag(flags?: Array<Record<string, string>> | null) {
  return flags?.find((flag) => flag.level === "high" && PERSISTENT_HIGH_RISK_CODES.has(flag.code));
}
