import { formatChinaDateTime } from "@/lib/format";

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

export function QuestionnaireSection({ questionnaire, editUrl }: { questionnaire: Questionnaire | null; editUrl?: string }) {
  const items = questionnaire?.latest_submission
    ? answerItems(questionnaire.fields, questionnaire.latest_submission.answers_json)
    : [];

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="font-medium">问卷结果</h2>
        <div className="flex flex-wrap items-center gap-3">
          {questionnaire?.latest_submission && (
            <span className="text-xs text-zinc-400">
              {formatDateTime(questionnaire.latest_submission.created_at)}
            </span>
          )}
          {questionnaire?.latest_submission && editUrl && (
            <a
              href={editUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-9 shrink-0 items-center rounded-md border border-cyan-200 bg-cyan-50 px-2.5 py-1 text-xs font-semibold text-cyan-700 hover:bg-cyan-100"
            >
              编辑并重新生成
            </a>
          )}
        </div>
      </div>

      {items.length > 0 ? (
        <div className="mt-3 max-h-[480px] overflow-y-auto space-y-3 pr-1 text-sm">
          {items.map((field) => (
            <div key={field.key} className="rounded-md border border-zinc-200 px-3 py-2">
              <div className="font-medium text-zinc-700">{field.label}</div>
              <div className="text-break mt-1 whitespace-pre-wrap leading-6 text-zinc-900">
                {field.value}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-sm text-zinc-500">暂未提交问卷</p>
      )}
    </section>
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
  return formatChinaDateTime(value, "暂无");
}
