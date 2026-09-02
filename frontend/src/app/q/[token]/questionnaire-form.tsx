"use client";

import { useMemo, useState } from "react";
import { Check, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Questionnaire = {
  token: string;
  template_key: string;
  language?: string;
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

type BodyIssue = { key: string; level: "hard" | "soft"; msg: string };
type Band = { hardLow: number; softLow: number; softHigh: number; hardHigh: number };

const BANDS: Record<"height" | "weight" | "age" | "bmi", Band> = {
  height: { hardLow: 120, softLow: 140, softHigh: 200, hardHigh: 220 },
  weight: {
    hardLow: 35,
    softLow: 40,
    softHigh: Number.POSITIVE_INFINITY,
    hardHigh: Number.POSITIVE_INFINITY,
  },
  age: { hardLow: 12, softLow: 16, softHigh: 70, hardHigh: 85 },
  bmi: {
    hardLow: 12,
    softLow: 15,
    softHigh: Number.POSITIVE_INFINITY,
    hardHigh: Number.POSITIVE_INFINITY,
  },
};

function classifyBand(v: number, b: Band): "hard" | "soft" | null {
  if (v < b.hardLow || v > b.hardHigh) return "hard";
  if (v < b.softLow || v > b.softHigh) return "soft";
  return null;
}

const DISSUADE = (label: string, v: number, unit: string, isEn: boolean) =>
  isEn
    ? `${label} is ${v}${unit}. This program is designed for general lifestyle intervention. We recommend consulting a healthcare provider or contacting your dedicated coach first.`
    : `${label}为 ${v}${unit}，本服务更适合常规减脂人群；您的情况建议先线下或专业医疗评估，请联系您的专属顾问`;

function checkBodyMeasurements(
  fields: Questionnaire["fields"],
  vals: Record<string, string>,
  isEn: boolean,
): BodyIssue[] {
  const find = (...parts: string[]): { key: string; num: number; raw: string } | null => {
    for (const f of fields) {
      const title = String(f.title ?? f.label ?? "").toLowerCase();
      if (parts.some((p) => title.includes(p.toLowerCase()))) {
        const raw = String(vals[f.key] ?? "");
        const m = raw.match(/\d+(?:\.\d+)?/);
        if (m) return { key: f.key, num: parseFloat(m[0]), raw };
      }
    }
    return null;
  };
  const weightKg = (value: { key: string; num: number; raw: string } | null) => {
    if (!value) return null;
    const text = value.raw.toLowerCase();
    const isLbs = text.includes("lb") || text.includes("pound") || text.includes("磅");
    if (isLbs) return { ...value, num: value.num * 0.45359237 };
    const isJin = text.includes("斤") && !["公斤", "千克", "kg", "㎏"].some((unit) => text.includes(unit));
    return { ...value, num: isJin ? value.num / 2 : value.num };
  };
  const waistCm = (value: { key: string; num: number; raw: string } | null) => {
    if (!value) return null;
    const text = value.raw.toLowerCase();
    if (text.includes("inch") || text.includes("in") || text.includes("英寸")) return { ...value, num: value.num * 2.54 };
    if (text.includes("mm") || text.includes("毫米")) return { ...value, num: value.num / 10 };
    if ((text.includes("m") && !text.includes("cm") && !text.includes("mm")) ||
        (text.includes("米") && !text.includes("厘米") && !text.includes("毫米"))) {
      return { ...value, num: value.num * 100 };
    }
    return value;
  };
  const issues: BodyIssue[] = [];
  const h = find("身高", "height");
  const w = weightKg(find("当前体重", "体重", "weight", "current weight"));
  const waist = waistCm(find("腰围", "waist"));
  const a = find("年龄", "age");

  if (h) {
    if (h.num >= 1.2 && h.num <= 2.5) {
      issues.push({
        key: h.key,
        level: "hard",
        msg: isEn
          ? `Height entered as ${h.num}. It appears to be in meters; please use centimeters (e.g., enter 170 for 1.7m).`
          : `身高填写为 ${h.num}，疑似填成了“米”，请改用厘米（如 1.7 米应填 170）`,
      });
    } else {
      const lv = classifyBand(h.num, BANDS.height);
      if (lv === "hard") {
        issues.push({
          key: h.key,
          level: "hard",
          msg: isEn
            ? `Height entered as ${h.num} cm is outside standard range. Please check for typos or unit errors.`
            : `身高填写为 ${h.num} cm，明显超出常规范围，请检查是否填错或单位有误`,
        });
      } else if (lv === "soft") {
        issues.push({
          key: h.key,
          level: "soft",
          msg: isEn
            ? `Height of ${h.num} cm deviates from typical range. Please confirm it is correct.`
            : `身高 ${h.num} cm 偏离常见范围，请确认填写无误`,
        });
      }
    }
  }
  if (w) {
    const lv = classifyBand(w.num, BANDS.weight);
    if (lv === "hard") issues.push({ key: w.key, level: "hard", msg: DISSUADE(isEn ? "Weight" : "体重", Number(w.num.toFixed(1)), " kg", isEn) });
    else if (lv === "soft") {
      issues.push({
        key: w.key,
        level: "soft",
        msg: isEn
          ? `Weight of ${w.num.toFixed(1)} kg deviates from typical range. Please confirm it is correct.`
          : `体重 ${w.num.toFixed(1)} kg 偏离常见范围，请确认填写无误`,
      });
    }
  }
  if (a) {
    const lv = classifyBand(a.num, BANDS.age);
    if (lv === "hard") issues.push({ key: a.key, level: "hard", msg: DISSUADE(isEn ? "Age" : "年龄", a.num, isEn ? " years old" : " 岁", isEn) });
    else if (lv === "soft") {
      issues.push({
        key: a.key,
        level: "soft",
        msg: isEn
          ? `Age ${a.num} requires special coaching attention. Please confirm it is correct.`
          : `年龄 ${a.num} 岁需重点关注，请确认填写无误`,
      });
    }
  }
  if (h && w && h.num > 0) {
    const hHard = issues.some((i) => i.key === h.key && i.level === "hard");
    const wHard = issues.some((i) => i.key === w.key && i.level === "hard");
    if (!hHard && !wHard) {
      const hm = h.num / 100;
      const bmi = w.num / (hm * hm);
      const lv = classifyBand(bmi, BANDS.bmi);
      if (lv) {
        const msg = lv === "hard"
          ? (isEn
              ? `Height and weight combination yields BMI ≈ ${bmi.toFixed(1)}, which appears contradictory. Please check your inputs.`
              : `身高体重组合 BMI≈${bmi.toFixed(1)}，明显矛盾，请检查身高或体重是否填错`)
          : (isEn
              ? `Height and weight combination yields BMI ≈ ${bmi.toFixed(1)}. Please confirm it is correct.`
              : `身高体重组合 BMI≈${bmi.toFixed(1)}，偏离常见范围，请确认填写无误`);
        issues.push({ key: h.key, level: lv, msg });
        issues.push({ key: w.key, level: lv, msg });
      }
    }
  }
  if (h && waist && h.num > 0) {
    const hHard = issues.some((i) => i.key === h.key && i.level === "hard");
    if (!hHard) {
      const ratio = waist.num / h.num;
      if (ratio < 0.2 || ratio > 1.2) {
        const msg = isEn
          ? `Waist-to-height ratio (≈${ratio.toFixed(2)}) appears contradictory. Please verify measurement and units.`
          : `腰围与身高组合（腰高比≈${ratio.toFixed(2)}）明显矛盾，请复测并检查是否填错或单位有误`;
        issues.push({ key: h.key, level: "hard", msg });
        issues.push({ key: waist.key, level: "hard", msg });
      }
    }
  }
  return issues;
}

function issuesByKey(issues: BodyIssue[]): Record<string, BodyIssue> {
  const m: Record<string, BodyIssue> = {};
  for (const it of issues) {
    if (!m[it.key] || (m[it.key].level === "soft" && it.level === "hard")) m[it.key] = it;
  }
  return m;
}

export function QuestionnaireForm({
  questionnaire,
  initialAnswers,
  editMode = false,
}: {
  questionnaire: Questionnaire;
  initialAnswers?: Record<string, string>;
  editMode?: boolean;
}) {
  const isEn = questionnaire.language === "en";
  const defaults = questionnaire.default_values ?? {};
  const [answers, setAnswers] = useState<Record<string, string>>(initialAnswers ?? defaults);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [fieldIssues, setFieldIssues] = useState<Record<string, BodyIssue>>({});
  const [softIssues, setSoftIssues] = useState<BodyIssue[]>([]);
  const [showSoftConfirm, setShowSoftConfirm] = useState(false);

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

  function scrollToField(key: string) {
    if (typeof document === "undefined") return;
    document.getElementById(`q-field-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function submitAnswers(nextAnswers: Record<string, string>, confirmed = false) {
    const missing = questionnaire.fields.find((field) => {
      if (!field.required || field.type === "SEGMENTATION") return false;
      return !String(nextAnswers[field.key] ?? "").trim();
    });
    if (missing) {
      setShowSoftConfirm(false);
      const title = missing.title ?? missing.label;
      const msg = isEn ? `Please fill in: ${title}` : `请填写：${title}`;
      setFieldIssues((prev) => ({
        ...prev,
        [missing.key]: { key: missing.key, level: "hard", msg },
      }));
      setError(msg);
      scrollToField(missing.key);
      return;
    }
    const tomorrow = new Date();
    tomorrow.setHours(0, 0, 0, 0);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const badDate = questionnaire.fields.find((field) => {
      if (field.type !== "DATE") return false;
      const v = String(nextAnswers[field.key] ?? "").trim();
      if (!v) return false;
      const picked = new Date(`${v}T00:00:00`);
      return Number.isNaN(picked.getTime()) || picked < tomorrow;
    });
    if (badDate) {
      setShowSoftConfirm(false);
      const msg = isEn
        ? "Start date must be at least tomorrow. Please select a valid start date."
        : "开始日期最早为次日（明天）起，请重新选择";
      setFieldIssues((prev) => ({
        ...prev,
        [badDate.key]: { key: badDate.key, level: "hard", msg },
      }));
      setError(msg);
      scrollToField(badDate.key);
      return;
    }
    const issues = checkBodyMeasurements(questionnaire.fields, nextAnswers, isEn);
    setFieldIssues(issuesByKey(issues));
    const hard = issues.filter((i) => i.level === "hard");
    if (hard.length) {
      setShowSoftConfirm(false);
      const count = new Set(hard.map((i) => i.key)).size;
      setError(isEn ? `Please correct ${count} item(s) marked below before submitting` : `有 ${count} 处填写需要修正，请按下方提示检查后再提交`);
      scrollToField(hard[0].key);
      return;
    }
    const soft = issues.filter((i) => i.level === "soft");
    if (soft.length && !confirmed) {
      setError(null);
      setSoftIssues(Object.values(issuesByKey(soft)));
      setShowSoftConfirm(true);
      scrollToField(soft[0].key);
      return;
    }
    setSubmitting(true);
    setError(null);
    setShowSoftConfirm(false);
    const url = `/api/questionnaires/${questionnaire.token}/submit?template=${encodeURIComponent(questionnaire.template_key)}`;
    try {
      const response = await fetch(url, {
        method: editMode ? "PATCH" : "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ answers: nextAnswers, language: questionnaire.language || "zh" }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? (isEn ? `Submission failed: ${caught.message}` : `提交失败：${caught.message}`)
          : (isEn ? "Submission failed: unable to connect to backend service" : "提交失败：无法连接后端服务")
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
      <div className="flex flex-col items-center gap-4 rounded-xl border border-slate-200 bg-white px-5 py-10 text-center shadow-sm sm:px-6 sm:py-12">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-50">
          <CheckCircle2 className="h-9 w-9 text-emerald-500" />
        </div>
        <h2 className="text-lg font-bold text-slate-900">
          {isEn
            ? (editMode ? "Assessment Updated" : "Assessment Submitted Successfully")
            : (editMode ? "问卷已更新" : "问卷已成功提交")}
        </h2>
        <p className="max-w-sm text-sm leading-6 text-slate-500">
          {isEn
            ? (editMode
                ? "Your answers have been saved. The system is regenerating your profile and reports."
                : "Thank you! Our coaching team and AI assistant are analyzing your responses. Your tailored diagnostic report and action plan will be delivered directly to your group chat shortly.")
            : (editMode
                ? "答案已保存，系统正在用新答案重新生成画像与报告，稍后可在文档列表查看待审核的新版本。"
                : "专属助理正在为您整理资料，请在社群中耐心等待，或联系您的专属顾问查看后续进展。")}
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-5 pb-28">
      {defaults.day_no && (
        <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-2.5 text-sm font-medium text-cyan-800">
          {isEn ? `Day ${defaults.day_no}` : `今天是第 ${defaults.day_no} 天`}
        </div>
      )}
      {questionnaire.template_key === "stage_review" && (defaults.initial_weight || defaults.initial_waist || defaults.morning_c_days) && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 space-y-1.5">
          <div className="text-sm font-semibold text-slate-700">{isEn ? "Reference Data" : "参考信息"}</div>
          {defaults.initial_weight && (
            <p className="text-sm text-slate-600">
              {isEn ? "Initial Weight: " : "初始体重："}
              {defaults.initial_weight}kg
              {answers.current_weight && ` → ${isEn ? "Current: " : "当前："}${answers.current_weight}kg`}
            </p>
          )}
          {defaults.initial_waist && (
            <p className="text-sm text-slate-600">
              {isEn ? "Initial Waist: " : "初始腰围："}
              {defaults.initial_waist}cm
              {answers.current_waist && ` → ${isEn ? "Current: " : "当前："}${answers.current_waist}cm`}
            </p>
          )}
        </div>
      )}

      {questionnaire.fields.map((field) => (
        <FieldInput
          key={field.key}
          field={field}
          isEn={isEn}
          value={answers[field.key]}
          issue={fieldIssues[field.key]}
          onChange={(value) => {
            setAnswers({ ...answers, [field.key]: value });
            if (fieldIssues[field.key]) {
              setFieldIssues((prev) => {
                const next = { ...prev };
                delete next[field.key];
                return next;
              });
            }
            if (showSoftConfirm) setShowSoftConfirm(false);
          }}
        />
      ))}

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-700">
          {error}
        </div>
      )}

      {showSoftConfirm && softIssues.length > 0 && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3.5">
          <div className="text-sm font-semibold text-amber-900">
            {isEn ? "The following values deviate from typical ranges, please confirm:" : "以下填写偏离常见范围，请再确认一下："}
          </div>
          <ul className="mt-2 space-y-1.5">
            {softIssues.map((it, idx) => (
              <li key={idx} className="flex gap-1.5 text-sm leading-6 text-amber-800">
                <span className="text-amber-500">•</span>
                <span>{it.msg}</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2.5">
            <Button
              type="button"
              className="h-10 bg-amber-600 px-5 hover:bg-amber-700"
              loading={submitting}
              loadingText={isEn ? "Submitting..." : "提交中…"}
              onClick={() => submitAnswers(answers, true)}
            >
              {isEn ? "Confirm & Submit" : "确认无误，继续提交"}
            </Button>
            <Button
              type="button"
              className="h-10 border border-slate-300 bg-white px-5 text-slate-700 shadow-none hover:bg-slate-50"
              onClick={() => {
                setShowSoftConfirm(false);
                if (softIssues[0]) scrollToField(softIssues[0].key);
              }}
            >
              {isEn ? "Go Back & Check" : "返回检查"}
            </Button>
          </div>
        </div>
      )}

      {/* 底部固定提交栏 */}
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-2xl items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between text-xs font-medium text-slate-500">
              <span>{isEn ? "Progress" : "完成进度"}</span>
              <span className="tabular-nums text-cyan-700">{answeredCount}/{answerableFields.length}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-cyan-600 transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
          <Button
            loading={submitting}
            loadingText={isEn ? "Submitting..." : "提交中…"}
            className="h-11 min-w-28 shrink-0 px-5 sm:px-6"
          >
            {isEn ? "Submit Assessment" : "提交问卷"}
          </Button>
        </div>
      </div>
    </form>
  );
}

function FieldInput({
  field,
  isEn,
  value,
  onChange,
  issue,
}: {
  field: Questionnaire["fields"][number];
  isEn: boolean;
  value?: string;
  onChange: (value: string) => void;
  issue?: BodyIssue;
}) {
  const id = `q-field-${field.key}`;
  const label = field.title ?? field.label;
  const subtitle = field.subtitle;

  if (field.type === "SEGMENTATION") {
    return (
      <section id={id} className="pt-3">
        <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-3.5">
          <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
            {label}
          </h2>
          {subtitle && (
            <p className="mt-1.5 text-sm leading-6 text-slate-600">{subtitle}</p>
          )}
        </div>
      </section>
    );
  }

  const required = field.required ? <span className="ml-1 text-red-500">*</span> : null;

  let control: React.ReactNode;
  if (field.type === "RADIO" || field.type === "CHECKBOX") {
    control = (
      <ChoiceField
        field={field}
        isEn={isEn}
        value={value}
        onChange={onChange}
        multi={field.type === "CHECKBOX"}
        label={label}
        subtitle={subtitle}
        required={required}
      />
    );
  } else if (field.type === "DATE") {
    control = (
      <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-2 text-sm font-semibold text-slate-900">{label}{required}</div>
        {subtitle && <div className="mb-2 text-xs leading-5 text-slate-500">{subtitle}</div>}
        <input
          type="date"
          required={field.required}
          value={value ?? ""}
          min={(() => { const d = new Date(); d.setDate(d.getDate() + 1); return d.toLocaleDateString("en-CA"); })()}
          max={(() => { const d = new Date(); d.setDate(d.getDate() + 7); return d.toLocaleDateString("en-CA"); })()}
          onChange={(event) => onChange(event.target.value)}
          className="h-11 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-100"
        />
        <div className="mt-2 text-xs leading-5 text-slate-500">
          {isEn ? "Earliest start date is tomorrow. Your 28-day journey begins from this date." : "开始日期最早为次日（明天）起；服务将从这一天起算第 1 天。"}
        </div>
      </label>
    );
  } else if (field.type === "SIMPLE" || field.type === "FILE") {
    control = (
      <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-2 text-sm font-semibold text-slate-900">{label}{required}</div>
        {subtitle && <div className="mb-2 text-xs leading-5 text-slate-500">{subtitle}</div>}
        <Input
          required={field.required}
          placeholder={field.type === "FILE" ? (isEn ? "File link or 'Will provide later'" : "请填写文件链接，或写“后续补充”。") : ""}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    );
  } else {
    control = (
      <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-2 text-sm font-semibold text-slate-900">{label}{required}</div>
        {subtitle && <div className="mb-2 text-xs leading-5 text-slate-500">{subtitle}</div>}
        <Textarea
          required={field.required}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    );
  }

  const ring = issue
    ? issue.level === "hard"
      ? "rounded-xl ring-2 ring-red-400"
      : "rounded-xl ring-2 ring-amber-400"
    : "";

  return (
    <div id={id} className={`scroll-mt-6 ${ring}`}>
      {control}
      {issue && (
        <p className={`mt-1.5 px-1 text-sm leading-6 ${issue.level === "hard" ? "text-red-600" : "text-amber-700"}`}>
          {issue.msg}
        </p>
      )}
    </div>
  );
}

const OTHER_OPTION = "其他";
const OTHER_OPTION_EN = "Other";

function ChoiceField({
  field,
  isEn,
  value,
  onChange,
  multi,
  label,
  subtitle,
  required,
}: {
  field: Questionnaire["fields"][number];
  isEn: boolean;
  value?: string;
  onChange: (value: string) => void;
  multi: boolean;
  label: string;
  subtitle?: string;
  required: React.ReactNode;
}) {
  const selected = (value ?? "").split("||").filter(Boolean);
  const isOther = (opt: string) =>
    opt === OTHER_OPTION ||
    opt === OTHER_OPTION_EN ||
    opt.startsWith(`${OTHER_OPTION}：`) ||
    opt.startsWith(`${OTHER_OPTION_EN}: `) ||
    opt.toLowerCase().startsWith("other:") ||
    opt.startsWith("其他：") ||
    opt.toLowerCase() === "other: ____" ||
    opt === "其他：____";
  const isOtherEntry = (s: string) => isOther(s);
  const otherEntry = selected.find(isOtherEntry);
  const otherChosen = Boolean(otherEntry);

  const [otherText, setOtherText] = useState(() => {
    if (!otherEntry) return "";
    if (otherEntry.includes(": ")) {
      return otherEntry.slice(otherEntry.indexOf(": ") + 2);
    }
    if (otherEntry.includes("：")) {
      return otherEntry.slice(otherEntry.indexOf("：") + 1);
    }
    return "";
  });

  const encodeOther = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return isEn ? OTHER_OPTION_EN : OTHER_OPTION;
    return isEn ? `${OTHER_OPTION_EN}: ${trimmed}` : `${OTHER_OPTION}：${trimmed}`;
  };

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
      {subtitle && <p className="mt-0.5 px-1 text-xs leading-5 text-slate-500">{subtitle}</p>}
      {multi && <p className="mt-0.5 px-1 text-xs text-cyan-600">{isEn ? "Select all that apply" : "可多选"}</p>}
      <div className="mt-2 grid gap-2">
        {(field.options ?? []).map((option) => {
          const active = isActive(option);
          const displayOption = option;
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
              <span className="text-break leading-6">{displayOption}</span>
            </label>
          );
        })}
      </div>
      {otherChosen && (
        <Input
          className="mt-2"
          placeholder={isEn ? "Please specify details..." : "请补充说明…"}
          value={otherText}
          onChange={(event) => updateOtherText(event.target.value)}
        />
      )}
    </fieldset>
  );
}
