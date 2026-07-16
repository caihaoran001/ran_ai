"use client";

import { useMemo, useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";
import {
  AlertCircle,
  CalendarClock,
  Check,
  CheckCircle2,
  FileQuestion,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Trash2,
} from "lucide-react";

type Template = {
  template_key: string;
  name: string;
  title?: string | null;
  description?: string | null;
  fields: Record<string, unknown>[];
};

type Config = {
  templates: Template[];
  schedule: Record<string, unknown>;
};

// 日打卡 H5 链接推送已整体下线；此配置现在只承载入营问卷提交后的自动回执。
type ScheduleConfig = {
  intake_submit_reply: {
    enabled: boolean;
    message: string;
  };
};

const DEFAULT_INTAKE_REPLY =
  "问卷已收到，正在生成专属方案。\n\n我们会基于你的填写结果生成：\n1.《个人能量重启处方单》\n2.《28天执行标准与打卡要求》\n\n生成完成后会第一时间发送给你，请稍等。";

const TEMPLATE_LABELS: Record<string, string> = {
  intake: "入营问卷",
  daily_checkin_standard: "打卡标准版",
  daily_checkin_simple: "打卡简版",
  stage_review: "阶段复盘",
};

function templateLabel(template: { template_key: string; name: string }) {
  return template.name || TEMPLATE_LABELS[template.template_key] || template.template_key;
}

export function QuestionnaireConfigEditor({ config }: { config: Config }) {
  const [templates, setTemplates] = useState(config.templates);
  const [activeKey, setActiveKey] = useState(config.templates[0]?.template_key ?? "intake");
  const [schedule, setSchedule] = useState<ScheduleConfig>(() => normalizeSchedule(config.schedule));
  const [message, setMessage] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const toastTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = (type: "success" | "error", msg: string) => {
    if (toastTimeoutRef.current) {
      clearTimeout(toastTimeoutRef.current);
    }
    setToast({ type, message: msg });
    toastTimeoutRef.current = setTimeout(() => {
      setToast(null);
    }, 4000);
  };

  useEffect(() => {
    return () => {
      if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    };
  }, []);

  const activeTemplate = useMemo(
    () => templates.find((template) => template.template_key === activeKey) ?? templates[0],
    [activeKey, templates],
  );

  async function save() {
    setMessage("");
    setSaveStatus("saving");
    const nextTemplates: Template[] = templates;

    try {
      const response = await fetch(`${API_BASE}/questionnaire-template/config`, {
        method: "PUT",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({ templates: nextTemplates, schedule: serializeSchedule(schedule) }),
      });
      if (response.ok) {
        setSaveStatus("success");
        showToast("success", "配置已成功保存！");
        setTimeout(() => setSaveStatus("idle"), 2500);
      } else {
        const errText = await response.text();
        setMessage(errText);
        showToast("error", `保存失败: ${errText}`);
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 4000);
      }
    } catch (e: any) {
      const errMsg = e.message || "未知错误";
      setMessage(errMsg);
      showToast("error", `网络或系统错误: ${errMsg}`);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 4000);
    }
  }

  const [resetting, setResetting] = useState(false);

  async function resetTemplates(templateKey?: string) {
    const label = templateKey
      ? `确定要将「${TEMPLATE_LABELS[templateKey] || templateKey}」还原为默认内容吗？`
      : "确定要将全部问卷还原为默认内容吗？当前修改将丢失。";
    if (!window.confirm(label)) return;

    setResetting(true);
    try {
      const url = templateKey
        ? `${API_BASE}/questionnaire-template/reset?template_key=${encodeURIComponent(templateKey)}`
        : `${API_BASE}/questionnaire-template/reset`;
      const response = await fetch(url, { method: "POST" });
      if (response.ok) {
        const data = await response.json();
        const newTemplates = data.templates as Template[];
        setTemplates(newTemplates);
        if (data.schedule) setSchedule(normalizeSchedule(data.schedule));
        showToast("success", templateKey ? "该问卷已还原为默认内容" : "全部问卷已还原为默认内容");
      } else {
        showToast("error", `还原失败: ${await response.text()}`);
      }
    } catch (e: any) {
      showToast("error", `还原失败: ${e.message || "未知错误"}`);
    } finally {
      setResetting(false);
    }
  }

  function updateTemplate(key: string, patch: Partial<Template>) {
    setTemplates((current) =>
      current.map((template) =>
        template.template_key === key ? { ...template, ...patch } : template,
      ),
    );
  }

  if (!activeTemplate) {
    return <section className="panel p-6 text-sm text-slate-500">暂无问卷配置</section>;
  }

  return (
    <div className="space-y-6">
      <section className="panel">
        <div className="panel-header sticky top-0 z-20 rounded-t-xl bg-white/95 backdrop-blur flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-cyan-50 text-cyan-700">
              <CalendarClock className="h-5 w-5" />
            </span>
            <div>
              <h2 className="panel-title">入营问卷回执</h2>
              <p className="panel-subtitle">
              用户提交入营问卷后机器人在群内自动回复。话术支持 {"{user_name}"}、{"{case_no}"}、{"{group_id}"} 变量。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              disabled={resetting}
              onClick={() => resetTemplates()}
              className="border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              一键还原全部
            </Button>
            <SaveButton saveStatus={saveStatus} onClick={save} label="保存配置" />
          </div>
        </div>
        <div className="p-5">
          <ScheduleEditor
            schedule={schedule}
            onChange={setSchedule}
          />
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
                <FileQuestion className="h-5 w-5" />
              </span>
              <div>
                <h2 className="panel-title">问卷内容</h2>
                <p className="panel-subtitle">每份问卷单独维护，不再把所有题目混在一个编辑框里。</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <SaveButton saveStatus={saveStatus} onClick={save} label="保存配置" />
              <Button
                type="button"
                disabled={resetting}
                onClick={() => resetTemplates(activeTemplate.template_key)}
                className="border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"
              >
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                还原此问卷
              </Button>
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            {templates.map((template) => (
              <button
                key={template.template_key}
                type="button"
                onClick={() => setActiveKey(template.template_key)}
                className={`rounded-md border px-3 py-2 text-sm font-semibold transition-colors duration-200 ${
                  activeKey === template.template_key
                    ? "border-cyan-700 bg-cyan-700 text-white shadow-sm"
                    : "border-slate-200 bg-white text-slate-700 hover:border-cyan-200 hover:bg-cyan-50/40"
                }`}
              >
                {templateLabel(template)}
                <span className="ml-2 text-xs opacity-70">{template.fields.length} 项</span>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-4 p-5">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-slate-700">配置名</div>
              <Input
                value={activeTemplate.name}
                onChange={(event) => updateTemplate(activeTemplate.template_key, { name: event.target.value })}
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-slate-700">页面标题</div>
              <Input
                value={activeTemplate.title ?? ""}
                onChange={(event) => updateTemplate(activeTemplate.template_key, { title: event.target.value })}
              />
            </label>
          </div>
          <label className="block">
            <div className="mb-2 text-sm font-semibold text-slate-700">页面说明</div>
            <Input
              value={activeTemplate.description ?? ""}
              onChange={(event) => updateTemplate(activeTemplate.template_key, { description: event.target.value })}
            />
          </label>
          <div className="block">
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-sm font-semibold text-slate-700">题目列表（共 {activeTemplate.fields.length} 题）</span>
              <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-mono text-slate-500">
                {activeTemplate.template_key}
              </span>
            </div>
            <FieldsEditor
              fields={activeTemplate.fields}
              onChange={(fields) => updateTemplate(activeTemplate.template_key, { fields })}
            />
          </div>
        </div>
      </section>
      
      {/* Floating Toast Notification */}
      {toast && (
        <div className="fixed right-6 top-6 z-50 flex max-w-md items-center gap-3 rounded-xl border border-slate-200/60 bg-white/80 p-4 shadow-xl backdrop-blur-md animate-toast-in">
          {toast.type === "success" ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0 animate-bounce" />
          ) : (
            <AlertCircle className="h-5 w-5 text-rose-500 shrink-0 animate-pulse" />
          )}
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-semibold text-slate-900">
              {toast.type === "success" ? "操作成功" : "配置错误"}
            </span>
            <span className="text-xs text-slate-600 leading-relaxed font-medium">
              {toast.message}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

type SaveStatus = "idle" | "saving" | "success" | "error";

function SaveButton({ saveStatus, onClick, label }: { saveStatus: SaveStatus; onClick: () => void; label: string }) {
  return (
    <Button
      type="button"
      onClick={onClick}
      disabled={saveStatus === "saving"}
      className={`shrink-0 transition-colors duration-200 ${
        saveStatus === "success"
          ? "bg-emerald-600 hover:bg-emerald-700 text-white"
          : saveStatus === "error"
          ? "bg-rose-600 hover:bg-rose-700 text-white"
          : "bg-cyan-700 hover:bg-cyan-800 text-white"
      }`}
    >
      {saveStatus === "saving" ? (
        <>
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          保存中...
        </>
      ) : saveStatus === "success" ? (
        <>
          <Check className="mr-2 h-4 w-4 animate-pulse" />
          已保存
        </>
      ) : saveStatus === "error" ? (
        <>保存失败</>
      ) : (
        <>
          <Save className="mr-2 h-4 w-4" />
          {label}
        </>
      )}
    </Button>
  );
}

function Switch({
  checked,
  onChange,
  disabled = false,
  size = "md",
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  size?: "md" | "lg";
}) {
  const isLg = size === "lg";
  const trackClass = isLg ? "h-6 w-11" : "h-5 w-9";
  const thumbClass = isLg ? "h-5 w-5" : "h-4 w-4";
  const translateClass = checked
    ? (isLg ? "translate-x-[22px]" : "translate-x-[18px]")
    : "translate-x-[2px]";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
      className={`relative inline-flex shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:ring-offset-2 ${trackClass} ${
        checked ? "bg-cyan-700 hover:bg-cyan-800" : "bg-slate-200 hover:bg-slate-300"
      } ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
    >
      <span
        className={`pointer-events-none inline-block rounded-full bg-white shadow-sm ring-0 transition-transform duration-200 ease-in-out ${thumbClass} ${translateClass}`}
      />
    </button>
  );
}

function ScheduleEditor({
  schedule,
  onChange,
}: {
  schedule: ScheduleConfig;
  onChange: (schedule: ScheduleConfig) => void;
}) {
  return (
    <div className="space-y-5">
      {/* 入营问卷提交后群内回执 */}
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
          <div>
            <h3 className="font-semibold text-slate-950">入营问卷提交后群内回执</h3>
            <p className="mt-1 text-xs text-slate-500">用户提交首份入营问卷后，机器人在群内自动回复一条确认与指引文案。</p>
          </div>
          <Switch
            checked={schedule.intake_submit_reply.enabled}
            onChange={(checked) =>
              onChange({
                ...schedule,
                intake_submit_reply: {
                  ...schedule.intake_submit_reply,
                  enabled: checked,
                },
              })
            }
          />
        </div>
        <div className={`p-4 pt-0 transition-all duration-300 ${
          schedule.intake_submit_reply.enabled
            ? "opacity-100"
            : "opacity-45 pointer-events-none select-none filter grayscale-[30%]"
        }`}>
          <div className="block">
            <div className="mb-2 flex items-center justify-between gap-3 text-xs font-medium text-slate-500">
              <span>回执话术</span>
              <div className="flex flex-wrap items-center justify-end gap-1">
                <span className="mr-1.5 text-xs text-slate-400">点击追加变量:</span>
                {["{user_name}", "{case_no}", "{group_id}"].map((varName) => (
                  <button
                    key={varName}
                    type="button"
                    disabled={!schedule.intake_submit_reply.enabled}
                    onClick={() =>
                      onChange({
                        ...schedule,
                        intake_submit_reply: {
                          ...schedule.intake_submit_reply,
                          message: schedule.intake_submit_reply.message + varName,
                        },
                      })
                    }
                    className="inline-flex items-center rounded-md border border-cyan-100 bg-cyan-50 px-2 py-0.5 font-mono text-xs font-semibold text-cyan-800 transition-colors hover:bg-cyan-100 disabled:opacity-40"
                  >
                    {varName}
                  </button>
                ))}
              </div>
            </div>
            <Textarea
              className="min-h-28 text-sm"
              value={schedule.intake_submit_reply.message}
              disabled={!schedule.intake_submit_reply.enabled}
              onChange={(event) =>
                onChange({
                  ...schedule,
                  intake_submit_reply: {
                    ...schedule.intake_submit_reply,
                    message: event.target.value,
                  },
                })
              }
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function normalizeSchedule(value: Record<string, unknown>): ScheduleConfig {
  // 只解析 intake_submit_reply；历史存库里的 rules/daily_rule/send_at 等旧字段在此丢弃。
  const intake =
    value.intake_submit_reply && typeof value.intake_submit_reply === "object"
      ? (value.intake_submit_reply as Record<string, unknown>)
      : null;
  return {
    intake_submit_reply: intake
      ? {
          enabled: intake.enabled !== false,
          message: String(intake.message ?? DEFAULT_INTAKE_REPLY),
        }
      : { enabled: true, message: DEFAULT_INTAKE_REPLY },
  };
}

function serializeSchedule(schedule: ScheduleConfig) {
  return { ...schedule };
}

const FIELD_TYPES: { value: string; label: string }[] = [
  { value: "SEGMENTATION", label: "分段标题" },
  { value: "SIMPLE", label: "单行文本" },
  { value: "TEXT", label: "多行文本" },
  { value: "RADIO", label: "单选" },
  { value: "CHECKBOX", label: "多选" },
  { value: "FILE", label: "文件上传" },
  { value: "DATE", label: "日期选择" },
];

function fieldStr(field: Record<string, unknown>, key: string): string {
  const value = field[key];
  return typeof value === "string" ? value : "";
}

function fieldOptions(field: Record<string, unknown>): string[] {
  return Array.isArray(field.options) ? field.options.map((option) => String(option)) : [];
}

function newField(): Record<string, unknown> {
  const id = `FIELD${Date.now()}${Math.random().toString(36).slice(2, 8)}`;
  return { id, key: id, type: "SIMPLE", title: "新题目", label: "新题目", subtitle: "", required: false, options: [] };
}

function FieldsEditor({
  fields,
  onChange,
}: {
  fields: Record<string, unknown>[];
  onChange: (fields: Record<string, unknown>[]) => void;
}) {
  function updateField(index: number, patch: Record<string, unknown>) {
    onChange(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)));
  }
  function moveField(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= fields.length) return;
    const next = [...fields];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }
  return (
    <div className="space-y-3">
      <div className="max-h-[560px] space-y-2 overflow-y-auto rounded-lg border border-slate-100 bg-slate-50/40 p-3">
        {fields.map((field, index) => (
          <FieldCard
            key={String(field.id || field.key || index)}
            field={field}
            index={index}
            total={fields.length}
            onUpdate={(patch) => updateField(index, patch)}
            onRemove={() => onChange(fields.filter((_, i) => i !== index))}
            onMove={(dir) => moveField(index, dir)}
          />
        ))}
        {fields.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-sm text-slate-400">
            还没有题目，点击下方“添加题目”开始。
          </div>
        )}
      </div>
      <Button
        type="button"
        className="w-full justify-center border border-cyan-200 bg-cyan-50 text-cyan-800 hover:bg-cyan-100"
        onClick={() => onChange([...fields, newField()])}
      >
        <Plus className="mr-1.5 h-4 w-4" /> 添加题目
      </Button>
    </div>
  );
}

function FieldCard({
  field,
  index,
  total,
  onUpdate,
  onRemove,
  onMove,
}: {
  field: Record<string, unknown>;
  index: number;
  total: number;
  onUpdate: (patch: Record<string, unknown>) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  const [open, setOpen] = useState(false);
  const type = fieldStr(field, "type") || "SIMPLE";
  const isSegment = type === "SEGMENTATION";
  const hasOptions = type === "RADIO" || type === "CHECKBOX";
  const options = fieldOptions(field);
  const typeLabel = FIELD_TYPES.find((item) => item.value === type)?.label ?? type;

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex cursor-pointer items-center gap-2 px-3 py-2.5" onClick={() => setOpen((value) => !value)}>
        <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-slate-100 px-1.5 text-xs font-semibold text-slate-500">
          {index + 1}
        </span>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${isSegment ? "bg-amber-50 text-amber-700" : "bg-cyan-50 text-cyan-700"}`}>
          {typeLabel}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{fieldStr(field, "title") || "（未命名）"}</span>
        {!isSegment && field.required === true && <span className="shrink-0 text-xs text-rose-500">*必填</span>}
        <div className="flex shrink-0 items-center gap-1" onClick={(event) => event.stopPropagation()}>
          <button type="button" title="上移" disabled={index === 0} onClick={() => onMove(-1)}
            className="rounded px-1.5 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30">↑</button>
          <button type="button" title="下移" disabled={index === total - 1} onClick={() => onMove(1)}
            className="rounded px-1.5 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30">↓</button>
          <button type="button" title="删除该题" onClick={onRemove}
            className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
        <span className="shrink-0 text-xs text-slate-400">{open ? "收起 ▲" : "展开 ▼"}</span>
      </div>
      {open && (
        <div className="border-t border-slate-100 p-4">
          <div className="grid gap-3 md:grid-cols-[160px_1fr]">
            <div>
              <div className="mb-1 text-xs font-medium text-slate-500">题型</div>
              <select
                className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm focus:border-cyan-700 focus:outline-none"
                value={type}
                onChange={(event) => {
                  const nextType = event.target.value;
                  const patch: Record<string, unknown> = { type: nextType };
                  if (nextType !== "RADIO" && nextType !== "CHECKBOX") patch.options = [];
                  else if (options.length === 0) patch.options = ["选项1", "选项2"];
                  onUpdate(patch);
                }}
              >
                {FIELD_TYPES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-slate-500">{isSegment ? "分段标题文字" : "题目"}</div>
              <Input value={fieldStr(field, "title")} onChange={(event) => onUpdate({ title: event.target.value, label: event.target.value })} />
            </div>
          </div>
          <div className="mt-3">
            <div className="mb-1 text-xs font-medium text-slate-500">说明（选填）</div>
            <Input value={fieldStr(field, "subtitle")} onChange={(event) => onUpdate({ subtitle: event.target.value })} />
          </div>
          {hasOptions && (
            <div className="mt-3">
              <div className="mb-1 text-xs font-medium text-slate-500">选项</div>
              <div className="space-y-2">
                {options.map((opt, optIndex) => (
                  <div key={optIndex} className="flex items-center gap-2">
                    <Input value={opt} onChange={(event) => onUpdate({ options: options.map((o, i) => (i === optIndex ? event.target.value : o)) })} />
                    <button type="button" title="删除选项"
                      onClick={() => onUpdate({ options: options.filter((_, i) => i !== optIndex) })}
                      className="shrink-0 rounded-md border border-slate-200 px-2 py-1.5 text-xs text-slate-500 hover:border-rose-300 hover:text-rose-600">删除</button>
                  </div>
                ))}
                <Button type="button"
                  className="border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => onUpdate({ options: [...options, `选项${options.length + 1}`] })}>
                  <Plus className="mr-1 h-3.5 w-3.5" /> 添加选项
                </Button>
              </div>
            </div>
          )}
          {!isSegment && (
            <div className="mt-3 flex items-center gap-2">
              <Switch checked={field.required === true} onChange={(checked) => onUpdate({ required: checked })} />
              <span className="text-sm text-slate-600">必填</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
