import Link from "next/link";
import { api, FRONTEND_BASE, ServiceCase, ServiceCaseStats, UserDocument, Checkin } from "@/lib/api";
import { CaseActions } from "./case-actions";

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
  const questionnaire = await api<Questionnaire>(`/questionnaires/${serviceCase.questionnaire_token}`).catch(() => null);
  const questionnaireUrl = `${FRONTEND_BASE.replace(/\/$/, "")}/q/${serviceCase.questionnaire_token}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{serviceCase.case_no}</h1>
        <p className="mt-1 text-sm text-zinc-500">{serviceCase.user?.name} · {serviceCase.group_id} · {serviceCase.status}</p>
        {serviceCase.deleted_at && (
          <p className="mt-2 inline-flex rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
            已删除服务单：{formatDateTime(serviceCase.deleted_at)}。该页面仅用于查看历史记录。
          </p>
        )}
      </div>
      {!serviceCase.deleted_at && <CaseActions caseId={id} />}
      <div className="grid gap-4 md:grid-cols-3">
        <Info title="问卷链接" value={questionnaireUrl} />
        <Info title="交付文件" value={`${documents.length} 份`} />
        <Info title="打卡记录" value={`${checkins.length} 条`} />
      </div>
      <div className="grid gap-4 md:grid-cols-4">
        <Info title="最后发言时间" value={formatDateTime(stats?.last_message_at)} />
        <Info title="用户最后发言" value={formatDateTime(stats?.last_user_message_at)} />
        <Info title="用户发言数" value={`${stats?.inbound_message_count ?? 0} 条`} />
        <Info title="日均用户发言" value={`${stats?.avg_daily_inbound_messages ?? 0} 条/天`} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="font-medium">问卷结果</h2>
          {questionnaire?.latest_submission ? (
            <div className="mt-3 space-y-3 text-sm">
              <div className="text-zinc-500">提交时间：{formatDateTime(questionnaire.latest_submission.created_at)}</div>
              {answerItems(questionnaire.fields, questionnaire.latest_submission.answers_json).map((field) => (
                <div key={field.key} className="rounded-md border border-zinc-200 px-3 py-2">
                  <div className="font-medium">{field.label}</div>
                  <div className="mt-1 whitespace-pre-wrap leading-6 text-zinc-700">
                    {field.value}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-sm text-zinc-500">暂未提交问卷</p>
          )}
        </section>
        <section className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="font-medium">交付文件</h2>
          <p className="mt-1 text-xs text-zinc-500">默认只显示每种文件的最新版本。</p>
          <div className="mt-3 grid gap-2 text-sm">
            {documents.map((document) => (
              <div key={document.id} className="flex items-center justify-between gap-3 rounded-md border border-zinc-200 px-3 py-2">
                <Link className="hover:underline" href={`/documents/${serviceCase.id}`}>
                  {document.title} · {document.status}
                </Link>
                {document.file_path && (
                  <a className="text-zinc-700 hover:underline" href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api"}/documents/${document.id}/download`}>
                    下载 docx
                  </a>
                )}
              </div>
            ))}
            {documents.length === 0 && <p className="text-zinc-500">还没有生成交付文件</p>}
          </div>
        </section>
        <section className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="font-medium">最近打卡</h2>
          <div className="mt-3 grid gap-2 text-sm">
            {checkins.slice(0, 5).map((checkin) => (
              <div key={checkin.id} className="rounded-md border border-zinc-200 px-3 py-2">
                <div className="text-zinc-500">{formatDateTime(checkin.created_at)}</div>
                <div>{checkin.content}</div>
              </div>
            ))}
            {checkins.length === 0 && <p className="text-zinc-500">暂无打卡</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

function formatAnswer(value: unknown) {
  if (Array.isArray(value)) return value.join("、");
  if (value === null || value === undefined) return "";
  return String(value).replaceAll("||", "、");
}

function answerItems(fields: Questionnaire["fields"], answers: Record<string, unknown>) {
  const knownKeys = new Set<string>();
  const items = fields
    .filter((field) => field.type !== "SEGMENTATION")
    .map((field) => {
      knownKeys.add(field.key);
      return {
        key: field.key,
        label: field.title ?? field.label,
        value: formatAnswer(answers[field.key]),
      };
    })
    .filter((item) => item.value);

  for (const [key, value] of Object.entries(answers)) {
    if (knownKeys.has(key)) continue;
    const display = formatAnswer(value);
    if (display) items.push({ key, label: key, value: display });
  }
  return items;
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

function Info({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="text-sm text-zinc-500">{title}</div>
      <div className="mt-2 break-all text-lg font-medium">{value}</div>
    </div>
  );
}
