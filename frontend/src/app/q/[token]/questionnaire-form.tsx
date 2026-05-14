"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Questionnaire = {
  token: string;
  fields: {
    key: string;
    type: string;
    label: string;
    title?: string;
    subtitle?: string;
    required?: boolean;
    options?: string[];
  }[];
};

export function QuestionnaireForm({ questionnaire }: { questionnaire: Questionnaire }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const missing = questionnaire.fields.find((field) => {
      if (!field.required || field.type === "SEGMENTATION") return false;
      return !String(answers[field.key] ?? "").trim();
    });
    if (missing) {
      setError(`请填写：${missing.title ?? missing.label}`);
      return;
    }
    setSubmitting(true);
    setError(null);
    const url = `/api/questionnaires/${questionnaire.token}/submit`;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({ answers }),
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

  if (done) {
    return <div className="rounded-lg border border-zinc-200 bg-white p-6">已提交，感谢配合。</div>;
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg border border-zinc-200 bg-white p-4">
      {questionnaire.fields.map((field) => (
        <FieldInput
          key={field.key}
          field={field}
          value={answers[field.key]}
          onChange={(value) => setAnswers({...answers, [field.key]: value})}
        />
      ))}
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <Button disabled={submitting}>{submitting ? "提交中" : "提交问卷"}</Button>
    </form>
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
      <section className="border-t border-zinc-200 pt-5 first:border-t-0 first:pt-0">
        <h2 className="text-lg font-semibold">{field.title ?? field.label}</h2>
        {field.subtitle && <p className="mt-1 text-sm text-zinc-500">{field.subtitle}</p>}
      </section>
    );
  }

  const label = field.title ?? field.label;
  const required = field.required ? <span className="ml-1 text-red-600">*</span> : null;

  if (field.type === "RADIO") {
    return (
      <fieldset className="block">
        <legend className="mb-2 text-sm font-medium">{label}{required}</legend>
        <div className="grid gap-2">
          {(field.options ?? []).map((option) => (
            <label key={option} className="flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm">
              <input
                type="radio"
                name={field.key}
                value={option}
                checked={value === option}
                onChange={() => onChange(option)}
                required={field.required}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  if (field.type === "CHECKBOX") {
    const selected = new Set((value ?? "").split("||").filter(Boolean));
    return (
      <fieldset className="block">
        <legend className="mb-2 text-sm font-medium">{label}{required}</legend>
        <div className="grid gap-2">
          {(field.options ?? []).map((option) => (
            <label key={option} className="flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm">
              <input
                type="checkbox"
                value={option}
                checked={selected.has(option)}
                onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) next.add(option);
                  else next.delete(option);
                  onChange(Array.from(next).join("||"));
                }}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  if (field.type === "SIMPLE" || field.type === "FILE") {
    return (
      <label className="block">
        <div className="mb-2 text-sm font-medium">{label}{required}</div>
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
    <label className="block">
      <div className="mb-2 text-sm font-medium">{label}{required}</div>
      <Textarea
        required={field.required}
        placeholder={field.type === "FILE" ? "请填写文件链接，或写“后续补充”。" : ""}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
