import { QuestionnaireForm } from "./questionnaire-form";
import { api } from "@/lib/api";

type Questionnaire = {
  service_case_id: number;
  token: string;
  user_name?: string | null;
  fields: {
    key: string;
    label: string;
    title?: string;
    subtitle?: string;
    type: string;
  }[];
  latest_submission?: {
    id: number;
    answers_json: Record<string, unknown>;
    created_at: string;
  } | null;
};

export default async function QuestionnairePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const questionnaire = await api<Questionnaire>(`/questionnaires/${token}`);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">燃AI服务问卷</h1>
        <p className="mt-1 text-sm text-zinc-500">
          {questionnaire.user_name ?? "用户"}，
          {questionnaire.latest_submission ? "以下是已提交的问卷内容。" : "请完成以下信息。"}
        </p>
      </div>
      {questionnaire.latest_submission ? (
        <QuestionnaireReadonly questionnaire={questionnaire} />
      ) : (
        <QuestionnaireForm questionnaire={questionnaire} />
      )}
    </div>
  );
}

function QuestionnaireReadonly({ questionnaire }: { questionnaire: Questionnaire }) {
  const answers = questionnaire.latest_submission?.answers_json ?? {};
  const answeredFields = answerItems(questionnaire.fields, answers);
  return (
    <section className="space-y-4 rounded-lg border border-zinc-200 bg-white p-4">
      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        已于 {formatDateTime(questionnaire.latest_submission?.created_at)} 提交。该链接现在仅用于查看，不支持修改或重复提交。
      </div>
      {answeredFields.map((field) => (
        <div key={field.key} className="rounded-md border border-zinc-200 p-3">
          <div className="text-sm font-medium">{field.label}</div>
          {field.subtitle && <div className="mt-1 text-xs text-zinc-500">{field.subtitle}</div>}
          <div className="mt-2 whitespace-pre-wrap text-sm leading-6">
            {field.value}
          </div>
        </div>
      ))}
    </section>
  );
}

function answerItems(
  fields: Questionnaire["fields"],
  answers: Record<string, unknown>,
) {
  const knownKeys = new Set<string>();
  const items = fields
    .filter((field) => field.type !== "SEGMENTATION")
    .map((field) => {
      knownKeys.add(field.key);
      return {
        key: field.key,
        label: field.title ?? field.label,
        subtitle: field.subtitle,
        value: formatAnswer(answers[field.key]),
      };
    })
    .filter((item) => item.value);

  for (const [key, value] of Object.entries(answers)) {
    if (knownKeys.has(key)) continue;
    const display = formatAnswer(value);
    if (display) items.push({ key, label: key, subtitle: undefined, value: display });
  }
  return items;
}

function formatAnswer(value: unknown) {
  if (Array.isArray(value)) return value.join("、");
  if (value === null || value === undefined) return "";
  return String(value).replaceAll("||", "、");
}

function formatDateTime(value?: string | null) {
  if (!value) return "未知时间";
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
