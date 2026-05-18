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

export function QuestionnaireSection({ questionnaire }: { questionnaire: Questionnaire | null }) {
  const items = questionnaire?.latest_submission
    ? answerItems(questionnaire.fields, questionnaire.latest_submission.answers_json)
    : [];

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">问卷结果</h2>
        {questionnaire?.latest_submission && (
          <span className="text-xs text-zinc-400">
            {formatDateTime(questionnaire.latest_submission.created_at)}
          </span>
        )}
      </div>

      {items.length > 0 ? (
        <div className="mt-3 max-h-[480px] overflow-y-auto space-y-3 pr-1 text-sm">
          {items.map((field) => (
            <div key={field.key} className="rounded-md border border-zinc-200 px-3 py-2">
              <div className="font-medium text-zinc-700">{field.label}</div>
              <div className="mt-1 whitespace-pre-wrap leading-6 text-zinc-900">
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
