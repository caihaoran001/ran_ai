import Link from "next/link";
import { api, FRONTEND_BASE, ServiceCase, ServiceCaseStats, UserDocument, Checkin, Message } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";
import { CaseActions } from "./case-actions";
import { CaseRiskReset } from "./case-risk-reset";
import { ChatMessages } from "./chat-messages";
import { QuestionnaireSection } from "./questionnaire-section";
import { StageReportActions } from "./stage-report-actions";

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
  const documents = await api<UserDocument[]>(`/service-cases/${id}/documents`).catch(() => []);
  const documentHistory = await api<UserDocument[]>(`/service-cases/${id}/documents?include_history=true`).catch(() => documents);
  const checkins = await api<Checkin[]>(`/service-cases/${id}/checkins`).catch(() => []);
  const stats = await api<ServiceCaseStats>(`/service-cases/${id}/stats`).catch(() => null);
  const messages = await api<Message[]>(`/service-cases/${id}/messages`).catch(() => []);
  const questionnaire = await api<Questionnaire>(`/questionnaires/${serviceCase.questionnaire_token}`).catch(() => null);
  const questionnaireUrl = `${FRONTEND_BASE.replace(/\/$/, "")}/q/${serviceCase.questionnaire_token}`;

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-zinc-900">{serviceCase.case_no}</h1>
            <StatusBadge status={serviceCase.status} />
          </div>
          <p className="mt-2 text-sm text-zinc-500">
            {serviceCase.user ? (
              <Link
                href={`/users/${serviceCase.user.id}`}
                className="font-semibold text-blue-600 hover:text-blue-700 hover:underline"
              >
                👤 {serviceCase.user.name} (查看完整用户画像与打卡历史)
              </Link>
            ) : (
              "待填写问卷用户"
            )}
             · {serviceCase.group_id}
          </p>
          {serviceCase.deleted_at && (
            <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-700">
              已删除：{formatDateTime(serviceCase.deleted_at)}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {serviceCase.risk_level === "high" && (
              <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-3 py-1 text-xs font-medium text-red-700">
                ⚠️ 高风险
                {serviceCase.risk_flags?.[0] && (
                  <span className="opacity-70">· {riskLabel(serviceCase.risk_flags[0])}</span>
                )}
              </span>
            )}
            {serviceCase.needs_human_intervention && (
              <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-3 py-1 text-xs font-medium text-orange-700">
                🙋 待人工介入
              </span>
            )}
            {(serviceCase.risk_level === "high" || serviceCase.needs_human_intervention) && (
              <CaseRiskReset caseId={serviceCase.id} />
            )}
          </div>
        </div>
        {!serviceCase.deleted_at && (
          <div className="flex flex-wrap justify-end gap-2">
            <CaseActions caseId={serviceCase.id} currentStartedAt={serviceCase.started_at} />
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard title="问卷链接" value={questionnaireUrl} icon="🔗" />
        <InfoCard title="交付文件" value={`${documents.length} 份`} icon="📄" />
        <InfoCard title="打卡记录" value={`${checkins.length} 条`} icon="✅" />
        <InfoCard title="聊天记录" value={`${messages.length} 条`} icon="💬" />
        <InfoCard title="已服务时长" value={formatServiceDuration(serviceCase.started_at)} icon="⏱️" />
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard title="群最后活跃" value={formatDateTime(stats?.last_message_at)} icon="🕐" />
        <InfoCard title="用户最后发言" value={formatDateTime(stats?.last_user_message_at)} icon="👤" />
        <InfoCard title="用户发言数" value={`${stats?.inbound_message_count ?? 0} 条`} icon="📝" />
        <InfoCard title="日均发言" value={`${stats?.avg_daily_inbound_messages ?? 0} 条/天`} icon="📊" />
        <InfoCard title="续购意向" value={formatIntentFlags(serviceCase.renewal_intent_flags)} icon="💚" />
        <InfoCard title="退款/退出意向" value={formatIntentFlags(serviceCase.refund_intent_flags)} icon="↩" />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <QuestionnaireSection questionnaire={questionnaire} />
        
        <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
          <div className="border-b border-zinc-100 px-6 py-4">
            <h2 className="font-semibold text-zinc-900">交付文件</h2>
          </div>
          <div className="p-6">
            {documents.length > 0 ? (
              <div className="space-y-3">
                {documents.map((document) => (
                  <div key={document.id} className="flex items-center justify-between rounded-lg border border-zinc-200 p-4 transition-colors hover:border-blue-300">
                    <Link className="font-medium text-zinc-900 hover:text-blue-600" href={`/documents/${document.id}`}>
                      {document.title}
                    </Link>
                    <div className="flex items-center gap-3">
                      <StatusBadge status={document.status} />
                      {document.file_path && (
                        <a
                          suppressHydrationWarning
                          className="text-sm text-blue-600 hover:text-blue-700"
                          href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api"}/documents/${document.id}/download`}
                        >
                          下载
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-8 text-center text-zinc-400">
                <div className="mb-2 text-4xl">📄</div>
                <p>还没有生成交付文件</p>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
          <div className="border-b border-zinc-100 px-6 py-4">
            <h2 className="font-semibold text-zinc-900">阶段报告</h2>
            <p className="mt-1 text-sm text-zinc-500">生成后会进入文档审核，可编辑、下载或发送。</p>
          </div>
          <div className="p-6">
            <StageReportActions serviceCaseId={serviceCase.id} documents={documentHistory} />
          </div>
        </div>

        <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
          <div className="border-b border-zinc-100 px-6 py-4">
            <h2 className="font-semibold text-zinc-900">最近打卡</h2>
          </div>
          <div className="p-6">
            {checkins.length > 0 ? (
              <div className="space-y-3">
                {checkins.slice(0, 5).map((checkin) => (
                  <div key={checkin.id} className="flex items-center gap-3 rounded-lg border border-zinc-100 p-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-green-100 text-green-700">✓</div>
                    <div className="flex-1">
                      <StructuredCheckin checkin={checkin} />
                      <div className="text-xs text-zinc-400">{formatDateTime(checkin.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-8 text-center text-zinc-400">
                <div className="mb-2 text-4xl">📋</div>
                <p>暂无打卡记录</p>
              </div>
            )}
          </div>
        </div>

        <ChatMessages messages={messages} />
      </div>
    </div>
  );
}

function StructuredCheckin({ checkin }: { checkin: Checkin }) {
  const parsed = checkin.parsed_json;
  if (!parsed) return <div className="text-sm text-zinc-900">{checkin.content}</div>;
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
  if (rows.length === 0) return <div className="text-sm text-zinc-900">{checkin.content}</div>;
  return (
    <div className="grid gap-1 text-sm text-zinc-900 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label}>
          <span className="text-zinc-500">{label}：</span>{value}
        </div>
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const statusConfig: Record<string, { label: string; className: string }> = {
    created: { label: "已创建", className: "bg-zinc-100 text-zinc-700" },
    questionnaire_submitted: { label: "已提交问卷", className: "bg-yellow-50 text-yellow-700" },
    documents_generated: { label: "已生成文档", className: "bg-blue-50 text-blue-700" },
    reviewing: { label: "审核中", className: "bg-purple-50 text-purple-700" },
    approved: { label: "已批准", className: "bg-green-50 text-green-700" },
    in_service: { label: "服务中", className: "bg-emerald-50 text-emerald-700" },
    completed: { label: "已完成", className: "bg-gray-50 text-gray-600" },
    sent: { label: "已发送", className: "bg-green-50 text-green-700" },
  };

  const config = statusConfig[status] || { label: status, className: "bg-zinc-100 text-zinc-700" };

  return (
    <span className={`rounded-full px-3 py-1 text-xs font-medium ${config.className}`}>
      {config.label}
    </span>
  );
}

function InfoCard({ title, value, icon }: { title: string; value: string; icon: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <span>{icon}</span>
        {title}
      </div>
      <div className="mt-2 break-all text-lg font-semibold text-zinc-900">{value}</div>
    </div>
  );
}

function formatDateTime(value?: string | null) {
  if (!value) return "暂无";
  return formatChinaDateTime(value, "暂无");
}

function formatServiceDuration(startedAt?: string | null) {
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

function formatRiskFlags(flags?: Array<Record<string, string>> | null) {
  if (!flags?.length) return "暂未记录具体原因，请查看聊天记录和问卷内容。";
  return flags.map((flag) => riskLabel(flag)).join("；");
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
    depression: "情绪危机/抑郁相关",
    hormone_medication: "激素类药物相关",
    liver_kidney: "肝肾异常相关",
    stone: "结石相关",
    severe_insomnia: "严重失眠相关",
    chest_pain: "胸痛/胸闷相关",
    palpitation: "心悸/心慌相关",
    allergy: "过敏相关",
    medical_claim: "医疗诊断/治疗承诺相关",
  };
  const label = labels[flag.code] || flag.code || "未知风险";
  return flag.keyword ? `${label}（命中：${flag.keyword}）` : label;
}
