import Link from "next/link";
import { api, FRONTEND_BASE, ServiceCase, ServiceCaseStats, UserDocument, Checkin, Message } from "@/lib/api";
import { CaseActions } from "./case-actions";
import { ChatMessages } from "./chat-messages";
import { QuestionnaireSection } from "./questionnaire-section";

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
            {serviceCase.user?.name} · {serviceCase.group_id}
          </p>
          {serviceCase.deleted_at && (
            <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-3 py-1 text-xs text-red-700">
              已删除：{formatDateTime(serviceCase.deleted_at)}
            </p>
          )}
        </div>
        {!serviceCase.deleted_at && <CaseActions />}
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard title="问卷链接" value={questionnaireUrl} icon="🔗" />
        <InfoCard title="交付文件" value={`${documents.length} 份`} icon="📄" />
        <InfoCard title="打卡记录" value={`${checkins.length} 条`} icon="✅" />
        <InfoCard title="聊天记录" value={`${messages.length} 条`} icon="💬" />
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard title="群最后活跃" value={formatDateTime(stats?.last_message_at)} icon="🕐" />
        <InfoCard title="用户最后发言" value={formatDateTime(stats?.last_user_message_at)} icon="👤" />
        <InfoCard title="用户发言数" value={`${stats?.inbound_message_count ?? 0} 条`} icon="📝" />
        <InfoCard title="日均发言" value={`${stats?.avg_daily_inbound_messages ?? 0} 条/天`} icon="📊" />
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
                    <Link className="font-medium text-zinc-900 hover:text-blue-600" href={`/documents/${serviceCase.id}`}>
                      {document.title}
                    </Link>
                    <div className="flex items-center gap-3">
                      <StatusBadge status={document.status} />
                      {document.file_path && (
                        <a className="text-sm text-blue-600 hover:text-blue-700" href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api"}/documents/${document.id}/download`}>
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
            <h2 className="font-semibold text-zinc-900">最近打卡</h2>
          </div>
          <div className="p-6">
            {checkins.length > 0 ? (
              <div className="space-y-3">
                {checkins.slice(0, 5).map((checkin) => (
                  <div key={checkin.id} className="flex items-center gap-3 rounded-lg border border-zinc-100 p-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-green-100 text-green-700">✓</div>
                    <div className="flex-1">
                      <div className="text-sm text-zinc-900">{checkin.content}</div>
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
