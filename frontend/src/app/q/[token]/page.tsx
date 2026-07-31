import { QuestionnaireForm } from "./questionnaire-form";
import { api, INGRESS_BASE } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

type Questionnaire = {
  service_case_id: number;
  token: string;
  template_key: string;
  title?: string | null;
  description?: string | null;
  allow_repeat: boolean;
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
  default_values?: Record<string, string>;
};

export default async function QuestionnairePage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>;
  searchParams?: Promise<{ template?: string; edit?: string }>;
}) {
  const { token } = await params;
  const sp = await searchParams;
  const template = sp?.template;
  const suffix = template ? `?template=${encodeURIComponent(template)}` : "";
  let questionnaire: Questionnaire;
  try {
    // 问卷展示是 C 端公开入口，走常驻 ingress，避开 backend 重启期的 502
    questionnaire = await api<Questionnaire>(`/questionnaires/${token}${suffix}`, undefined, INGRESS_BASE);
  } catch (error) {
    const message = String(error);
    if (template && (message.includes("410") || message.includes("28天服务已结束"))) {
      return <GraduatedCheckinPage />;
    }
    throw error;
  }
  const editMode = sp?.edit === "1" && Boolean(questionnaire.latest_submission);
  const isReadonly = Boolean(questionnaire.latest_submission && !questionnaire.allow_repeat) && !editMode;
  const initialAnswers: Record<string, string> = {};
  if (editMode && questionnaire.latest_submission) {
    for (const [k, v] of Object.entries(questionnaire.latest_submission.answers_json)) {
      initialAnswers[k] = Array.isArray(v) ? v.join("||") : v == null ? "" : String(v);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-2xl px-4 py-5 sm:py-8">
        <header className="mb-5 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm sm:mb-6">
          <div className="bg-cyan-700 px-4 py-4 text-white sm:px-5 sm:py-5">
            <div className="text-xs font-semibold text-cyan-100">
              燃人AI · 高燃人生
            </div>
            <h1 className="text-break mt-1.5 text-xl font-bold leading-snug sm:text-2xl">
              {questionnaire.title ?? "燃人AI服务问卷"}
            </h1>
          </div>
          <p className="px-4 py-3.5 text-sm leading-6 text-slate-600 sm:px-5">
            {questionnaire.user_name ? `${questionnaire.user_name}，` : ""}
            {editMode ? "编辑模式：修改答案后点击提交，系统会用新答案重新生成画像与报告。" : isReadonly ? "以下是您已提交的问卷内容。" : (questionnaire.description ?? "请完成以下信息。")}
          </p>
        </header>
        {isReadonly ? (
          <QuestionnaireReadonly questionnaire={questionnaire} />
        ) : (
          <QuestionnaireForm
            questionnaire={questionnaire}
            editMode={editMode}
            initialAnswers={editMode ? initialAnswers : undefined}
          />
        )}
      </div>
    </div>
  );
}

function GraduatedCheckinPage() {
  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto flex min-h-screen max-w-2xl items-center px-4 py-10">
        <section className="w-full rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 text-lg font-bold text-emerald-700">
            ✓
          </div>
          <h1 className="mt-4 text-xl font-bold text-slate-950">28天服务已结束</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            您已完成本期服务，无需继续提交每日打卡。历史记录已保留，后续问题请在群内联系班主任。
          </p>
        </section>
      </div>
    </div>
  );
}

function QuestionnaireReadonly({ questionnaire }: { questionnaire: Questionnaire }) {
  const answers = questionnaire.latest_submission?.answers_json ?? {};
  const answeredFields = answerItems(questionnaire.fields, answers);
  return (
    <section className="space-y-3">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm leading-6 text-emerald-800">
        已于 {formatDateTime(questionnaire.latest_submission?.created_at)} 提交。该链接现在仅用于查看，不支持修改或重复提交。
      </div>
      {answeredFields.map((field) => (
        <div key={field.key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">{field.label}</div>
          {field.subtitle && <div className="mt-1 text-xs text-slate-500">{field.subtitle}</div>}
          <div className="text-break mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">
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
  return formatChinaDateTime(value, "未知时间");
}
