"use client";

import { useMemo, useState } from "react";
import { Check, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { CAI_HAORAN_SAMPLE_ANSWERS } from "./sample-answers";

type Questionnaire = {
  token: string;
  template_key: string;
  fields: {
    key: string;
    type: string;
    label: string;
    title?: string;
    subtitle?: string;
    required?: boolean;
    options?: string[];
  }[];
  default_values?: Record<string, string>;
};

export function QuestionnaireForm({ questionnaire }: { questionnaire: Questionnaire }) {
  const defaults = questionnaire.default_values ?? {};
  const [answers, setAnswers] = useState<Record<string, string>>(defaults);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const answerableFields = useMemo(
    () => questionnaire.fields.filter((field) => field.type !== "SEGMENTATION"),
    [questionnaire.fields],
  );
  const answeredCount = answerableFields.filter(
    (field) => String(answers[field.key] ?? "").trim(),
  ).length;
  const progress = answerableFields.length
    ? Math.round((answeredCount / answerableFields.length) * 100)
    : 0;

  async function submitAnswers(nextAnswers: Record<string, string>) {
    const missing = questionnaire.fields.find((field) => {
      if (!field.required || field.type === "SEGMENTATION") return false;
      return !String(nextAnswers[field.key] ?? "").trim();
    });
    if (missing) {
      setError(`请填写：${missing.title ?? missing.label}`);
      return;
    }
    setSubmitting(true);
    setError(null);
    const url = `/api/questionnaires/${questionnaire.token}/submit?template=${encodeURIComponent(questionnaire.template_key)}`;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({ answers: nextAnswers }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? `提交失败：${caught.message}`
          : "提交失败：无法连接后端服务"
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await submitAnswers(answers);
  }

  if (done) {
    return (
      <div className="flex flex-col items-center gap-4 rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center shadow-sm">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-50">
          <CheckCircle2 className="h-9 w-9 text-emerald-500" />
        </div>
        <h2 className="text-lg font-bold text-slate-900">问卷已成功提交</h2>
        <p className="max-w-sm text-sm leading-6 text-slate-500">
          专属助理正在为您整理资料，请在社群中耐心等待，或联系您的专属顾问查看后续进展。
        </p>
      </div>
    );
  }

  function fillCaiHaoranSample() {
    const sample = caiHaoranSampleAnswers(questionnaire);
    setAnswers(sample);
    setError(null);
  }

  async function submitCaiHaoranSample() {
    const sample = caiHaoranSampleAnswers(questionnaire);
    setAnswers(sample);
    await submitAnswers(sample);
  }

  void fillCaiHaoranSample;
  void submitCaiHaoranSample;

  return (
    <form onSubmit={submit} className="space-y-5 pb-28">
      {defaults.day_no && (
        <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-2.5 text-sm font-medium text-cyan-800">
          今天是第 {defaults.day_no} 天
        </div>
      )}
      {questionnaire.template_key === "stage_review" && (defaults.initial_weight || defaults.initial_waist || defaults.morning_c_days) && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 space-y-1.5">
          <div className="text-sm font-semibold text-slate-700">参考信息</div>
          {defaults.initial_weight && (
            <p className="text-sm text-slate-600">
              初始体重：{defaults.initial_weight}kg
              {answers.current_weight && ` → 当前：${answers.current_weight}kg`}
            </p>
          )}
          {defaults.initial_waist && (
            <p className="text-sm text-slate-600">
              初始腰围：{defaults.initial_waist}cm
              {answers.current_waist && ` → 当前：${answers.current_waist}cm`}
            </p>
          )}
          {(defaults.morning_c_days || defaults.noon_b_days || defaults.night_a_days) && (
            <p className="text-sm text-slate-600">
              过去7天产品完成：早C {defaults.morning_c_days || "0/7天"}，午B {defaults.noon_b_days || "0/7天"}，晚A {defaults.night_a_days || "0/7天"}
            </p>
          )}
        </div>
      )}

      {questionnaire.fields.map((field) => (
        <FieldInput
          key={field.key}
          field={field}
          value={answers[field.key]}
          onChange={(value) => setAnswers({...answers, [field.key]: value})}
        />
      ))}

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-700">
          {error}
        </div>
      )}

      {/* 底部固定提交栏 */}
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/90 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-2xl items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500">
              <span>完成进度</span>
              <span className="tabular-nums text-cyan-700">{answeredCount}/{answerableFields.length}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-cyan-600 transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
          <Button disabled={submitting} className="h-11 min-w-28 px-6">
            {submitting ? "提交中…" : "提交问卷"}
          </Button>
        </div>
      </div>
    </form>
  );
}

function caiHaoranSampleAnswers(questionnaire: Questionnaire): Record<string, string> {
  const availableKeys = new Set(questionnaire.fields.map((field) => field.key));
  return Object.fromEntries(
    Object.entries(CAI_HAORAN_SAMPLE_ANSWERS).filter(([key]) => availableKeys.has(key)),
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: Questionnaire["fields"][number];
  value?: string;
  onChange: (value: string) => void;
}) {
  if (field.type === "SEGMENTATION") {
    return (
      <section className="pt-3">
        <div className="rounded-xl border border-slate-200 bg-gradient-to-br from-cyan-50 to-white px-4 py-3.5">
          <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
            <span className="h-4 w-1 rounded-full bg-cyan-600" />
            {field.title ?? field.label}
          </h2>
          {field.subtitle && (
            <p className="mt-1.5 pl-3 text-sm leading-6 text-slate-500">{field.subtitle}</p>
          )}
        </div>
      </section>
    );
  }

  const label = field.title ?? field.label;
  const required = field.required ? <span className="ml-1 text-red-500">*</span> : null;

  if (field.type === "RADIO" || field.type === "CHECKBOX") {
    return (
      <ChoiceField
        field={field}
        value={value}
        onChange={onChange}
        multi={field.type === "CHECKBOX"}
        label={label}
        required={required}
      />
    );
  }

  if (field.type === "SIMPLE" || field.type === "FILE") {
    return (
      <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-2 text-sm font-semibold text-slate-900">{label}{required}</div>
        {field.subtitle && <div className="mb-2 text-xs leading-5 text-slate-400">{field.subtitle}</div>}
        <Input
          required={field.required}
          placeholder={field.type === "FILE" ? "请填写文件链接，或写“后续补充”。" : ""}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    );
  }

  return (
    <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 text-sm font-semibold text-slate-900">{label}{required}</div>
      {field.subtitle && <div className="mb-2 text-xs leading-5 text-slate-400">{field.subtitle}</div>}
      <Textarea
        required={field.required}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

const OTHER_OPTION = "其他";

// 单选/多选选项组，选中"其他"时展开补充输入框；补充内容编码为「其他：xxx」存入答案
function ChoiceField({
  field,
  value,
  onChange,
  multi,
  label,
  required,
}: {
  field: Questionnaire["fields"][number];
  value?: string;
  onChange: (value: string) => void;
  multi: boolean;
  label: string;
  required: React.ReactNode;
}) {
  const selected = (value ?? "").split("||").filter(Boolean);
  const isOtherEntry = (s: string) => s === OTHER_OPTION || s.startsWith(`${OTHER_OPTION}：`);
  const otherEntry = selected.find(isOtherEntry);
  const otherChosen = Boolean(otherEntry);
  const [otherText, setOtherText] = useState(
    otherEntry && otherEntry.startsWith(`${OTHER_OPTION}：`)
      ? otherEntry.slice(OTHER_OPTION.length + 1)
      : "",
  );

  const isOther = (opt: string) => opt === OTHER_OPTION;
  const encodeOther = (text: string) => (text.trim() ? `${OTHER_OPTION}：${text.trim()}` : OTHER_OPTION);
  const isActive = (opt: string) => (isOther(opt) ? otherChosen : selected.includes(opt));

  function toggle(opt: string) {
    if (!multi) {
      onChange(isOther(opt) ? encodeOther(otherText) : opt);
      return;
    }
    const wasActive = isActive(opt);
    const next = selected.filter((s) => (isOther(opt) ? !isOtherEntry(s) : s !== opt));
    if (!wasActive) next.push(isOther(opt) ? encodeOther(otherText) : opt);
    onChange(next.join("||"));
  }

  function updateOtherText(text: string) {
    setOtherText(text);
    if (!otherChosen) return;
    if (!multi) {
      onChange(encodeOther(text));
      return;
    }
    onChange(selected.map((s) => (isOtherEntry(s) ? encodeOther(text) : s)).join("||"));
  }

  return (
    <fieldset className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <legend className="px-1 text-sm font-semibold text-slate-900">{label}{required}</legend>
      {field.subtitle && <p className="mt-0.5 px-1 text-xs leading-5 text-slate-400">{field.subtitle}</p>}
      {multi && <p className="mt-0.5 px-1 text-xs text-cyan-600">可多选</p>}
      <div className="mt-2 grid gap-2">
        {(field.options ?? []).map((option) => {
          const active = isActive(option);
          return (
            <label
              key={option}
              className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3.5 py-2.5 text-sm transition-colors ${
                active
                  ? "border-cyan-600 bg-cyan-50 text-cyan-900"
                  : "border-slate-200 text-slate-700 hover:border-slate-300 hover:bg-slate-50"
              }`}
            >
              <input
                type={multi ? "checkbox" : "radio"}
                className="sr-only"
                name={field.key}
                checked={active}
                onChange={() => toggle(option)}
              />
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center border-2 ${multi ? "rounded-md" : "rounded-full"} ${
                  active ? (multi ? "border-cyan-600 bg-cyan-600" : "border-cyan-600") : "border-slate-300"
                }`}
              >
                {active && (multi
                  ? <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
                  : <span className="h-2.5 w-2.5 rounded-full bg-cyan-600" />)}
              </span>
              <span className="leading-6">{option}</span>
            </label>
          );
        })}
      </div>
      {otherChosen && (
        <Input
          className="mt-2"
          placeholder="请补充说明…"
          value={otherText}
          onChange={(event) => updateOtherText(event.target.value)}
        />
      )}
    </fieldset>
  );
}
