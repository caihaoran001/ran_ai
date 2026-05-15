"use client";

import { useState } from "react";

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
  const [expanded, setExpanded] = useState(false);
  const items = questionnaire?.latest_submission
    ? answerItems(questionnaire.fields, questionnaire.latest_submission.answers_json)
    : [];
  const firstItem = items[0];

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between"
      >
        <h2 className="font-medium">问卷结果</h2>
        <span className="text-sm text-zinc-500">
          {questionnaire?.latest_submission ? (expanded ? "收起" : `展开全部 ${items.length} 项`) : "暂未提交"}
        </span>
      </button>
      {!expanded && firstItem && (
        <div className="mt-3 rounded-md border border-zinc-200 px-3 py-2 text-sm">
          <div className="font-medium">{firstItem.label}</div>
          <div className="mt-1 whitespace-pre-wrap leading-6 text-zinc-700 line-clamp-2">
            {firstItem.value}
          </div>
          {items.length > 1 && (
            <div className="mt-1 text-xs text-zinc-400">还有 {items.length - 1} 项...</div>
          )}
        </div>
      )}
      {expanded && questionnaire?.latest_submission && (
        <div className="mt-3 space-y-3 text-sm">
          <div className="text-zinc-500">
            提交时间：{formatDateTime(questionnaire.latest_submission.created_at)}
          </div>
          {items.map((field) => (
            <div key={field.key} className="rounded-md border border-zinc-200 px-3 py-2">
              <div className="font-medium">{field.label}</div>
              <div className="mt-1 whitespace-pre-wrap leading-6 text-zinc-700">
                {field.value}
              </div>
            </div>
          ))}
        </div>
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
