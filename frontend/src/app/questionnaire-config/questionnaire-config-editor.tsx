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
  RefreshCw,
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

type ScheduleRule = {
  day: number;
  template_key: string;
  message: string;
};

type DailyRule = {
  enabled: boolean;
  template_key: string;
  message: string;
  skip_days: number[];
};

type ScheduleConfig = {
  enabled: boolean;
  send_at: string;
  timezone: string;
  intake_submit_reply: {
    enabled: boolean;
    message: string;
  };
  inactive_followup: {
    enabled: boolean;
    hours: number;
    message: string;
  };
  rules_enabled: boolean;
  rules: ScheduleRule[];
  daily_rule: DailyRule;
};

const TEMPLATE_LABELS: Record<string, string> = {
  intake: "入营问卷",
  daily_checkin_standard: "打卡标准版",
  daily_checkin_simple: "打卡简版",
  stage_review: "阶段复盘",
};

const SCHEDULE_MESSAGE_VARIABLES = [
  "{day}",
  "{personalized_link}",
  "{questionnaire_link}",
  "{user_name}",
];

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
              <h2 className="panel-title">发送计划</h2>
              <p className="panel-subtitle">
              文案支持 {"{personalized_link}"}、{"{questionnaire_link}"}、{"{day}"}、{"{user_name}"}、{"{case_no}"}、{"{group_id}"}、{"{template_key}"}。
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
            templates={templates}
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
  templates,
  onChange,
}: {
  schedule: ScheduleConfig;
  templates: Template[];
  onChange: (schedule: ScheduleConfig) => void;
}) {
  const questionnaireTemplates = templates.filter((template) => template.template_key !== "intake");
  const [highlightedIndex, setHighlightedIndex] = useState<number | null>(null);
  const prevRulesLength = useRef(schedule.rules.length);
  const rulesContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (schedule.rules.length > prevRulesLength.current) {
      const newIndex = schedule.rules.length - 1;
      setHighlightedIndex(newIndex);
      
      setTimeout(() => {
        if (rulesContainerRef.current) {
          const children = rulesContainerRef.current.children;
          if (children && children.length > 0) {
            const lastChild = children[children.length - 1];
            lastChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        }
      }, 100);

      const timer = setTimeout(() => {
        setHighlightedIndex(null);
      }, 2000);
      return () => clearTimeout(timer);
    }
    prevRulesLength.current = schedule.rules.length;
  }, [schedule.rules.length]);

  function updateRule(index: number, patch: Partial<ScheduleRule>) {
    onChange({
      ...schedule,
      rules: schedule.rules.map((rule, current) =>
        current === index ? { ...rule, ...patch } : rule,
      ),
    });
  }

  function removeRule(index: number) {
    onChange({
      ...schedule,
      rules: schedule.rules.filter((_, current) => current !== index),
    });
  }

  return (
    <div className="space-y-5">
      {/* 一、全局总控 Banner 开关 */}
      <div className="rounded-xl border border-cyan-200 bg-cyan-50/60 p-5 shadow-sm transition-colors duration-200">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold text-slate-950">自动发送问卷计划总开关</h3>
            <p className="max-w-2xl text-xs leading-6 text-slate-600">
              控制整个运营系统的自动发卷服务。关闭后，下方所有的定点规则、回执确认等逻辑都将彻底挂起，不会向当前消息通道发送任何消息。
            </p>
          </div>
          <Switch
            checked={schedule.enabled}
            size="lg"
            onChange={(checked) => onChange({ ...schedule, enabled: checked })}
          />
        </div>
      </div>

      {/* 当总开关关闭时的视觉提醒 */}
      {!schedule.enabled && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 flex items-center gap-2 animate-fade-in">
          <AlertCircle className="h-4 w-4 text-amber-600 shrink-0" />
          <span><strong>自动发卷服务当前未激活</strong>。下方所有计划配置与时间规则目前均处于挂起置灰状态，不会触发群聊推送。</span>
        </div>
      )}

      {/* 全局锁定遮罩联动 */}
      <div className={`space-y-5 transition-all duration-300 ${
        schedule.enabled
          ? "opacity-100"
          : "opacity-45 pointer-events-none select-none filter grayscale-[30%]"
      }`}>
        
        {/* 全局发送时间和时区 */}
        <div className="grid gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-2">
          <div className="block">
            <div className="mb-2 text-sm font-semibold text-slate-700">统一发送时间</div>
            <select
              className="flex h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus:border-cyan-700 focus-visible:outline-none"
              value={schedule.send_at}
              disabled={!schedule.enabled}
              onChange={(event) => onChange({ ...schedule, send_at: event.target.value })}
            >
              <option value="随时">随时发送（立即发送）</option>
              {Array.from({ length: 35 }).map((_, i) => {
                const hour = Math.floor(i / 2) + 6;
                const minute = i % 2 === 0 ? "00" : "30";
                const timeString = `${hour.toString().padStart(2, "0")}:${minute}`;
                return (
                  <option key={timeString} value={timeString}>
                    {timeString}
                  </option>
                );
              })}
            </select>
            <p className="mt-1.5 text-xs text-slate-400">系统定时轮询并自动发卷的时间点。</p>
          </div>
          <div className="block">
            <div className="mb-2 text-sm font-semibold text-slate-700">系统时区</div>
            <select
              className="flex h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus:border-cyan-700 focus-visible:outline-none"
              value={schedule.timezone}
              disabled={!schedule.enabled}
              onChange={(event) => onChange({ ...schedule, timezone: event.target.value })}
            >
              <option value="Asia/Shanghai">北京时间 (Asia/Shanghai)</option>
            </select>
            <p className="mt-1.5 text-xs text-slate-400">定时推送时间所采用的时区计算标准。</p>
          </div>
        </div>

        {/* 入营问卷提交后群内回执 */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
            <div>
              <h3 className="font-semibold text-slate-950">入营问卷提交后群内回执</h3>
              <p className="mt-1 text-xs text-slate-500">用户提交首份入营问卷后，机器人在群内自动回复一条确认与指引文案。</p>
            </div>
            <Switch
              checked={schedule.intake_submit_reply.enabled}
              disabled={!schedule.enabled}
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

        {/* 长时间无互动后的温和唤醒 */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
            <div>
              <h3 className="font-semibold text-slate-950">无互动自动唤醒</h3>
              <p className="mt-1 text-xs text-slate-500">
                服务中的用户超过指定小时数没有在群内发言时，每天最多由机器人温和提醒一次。
              </p>
            </div>
            <Switch
              checked={schedule.inactive_followup.enabled}
              disabled={!schedule.enabled}
              onChange={(checked) =>
                onChange({
                  ...schedule,
                  inactive_followup: {
                    ...schedule.inactive_followup,
                    enabled: checked,
                  },
                })
              }
            />
          </div>
          <div className={`space-y-4 p-4 pt-0 transition-all duration-300 ${
            schedule.inactive_followup.enabled
              ? "opacity-100"
              : "opacity-45 pointer-events-none select-none filter grayscale-[30%]"
          }`}>
            <label className="block max-w-xs">
              <div className="mb-2 text-sm font-semibold text-slate-700">超过多少小时无互动后提醒</div>
              <Input
                type="number"
                min={1}
                value={schedule.inactive_followup.hours}
                disabled={!schedule.inactive_followup.enabled}
                onChange={(event) =>
                  onChange({
                    ...schedule,
                    inactive_followup: {
                      ...schedule.inactive_followup,
                      hours: Math.max(1, Number(event.target.value) || 24),
                    },
                  })
                }
              />
            </label>
            <label className="block">
              <div className="mb-2 flex items-center justify-between gap-3 text-xs font-medium text-slate-500">
                <span>唤醒话术</span>
                <div className="flex flex-wrap items-center justify-end gap-1">
                  <span className="mr-1.5 text-xs text-slate-400">点击追加变量:</span>
                  {["{user_name}", "{case_no}", "{group_id}", "{hours}"].map((varName) => (
                    <button
                      key={varName}
                      type="button"
                      disabled={!schedule.inactive_followup.enabled}
                      onClick={() =>
                        onChange({
                          ...schedule,
                          inactive_followup: {
                            ...schedule.inactive_followup,
                            message: schedule.inactive_followup.message + varName,
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
                className="min-h-24 text-sm"
                value={schedule.inactive_followup.message}
                disabled={!schedule.inactive_followup.enabled}
                onChange={(event) =>
                  onChange({
                    ...schedule,
                    inactive_followup: {
                      ...schedule.inactive_followup,
                      message: event.target.value,
                    },
                  })
                }
              />
            </label>
          </div>
        </div>

        {/* 二、指定服务日发送（带独立开关） */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 bg-slate-50/60">
            <div>
              <h3 className="font-semibold text-slate-950">指定服务日发送</h3>
              <p className="mt-1 text-xs text-slate-500">在指定的具体服务日（第几天），向群组自动推送特定的打卡/复盘问卷。</p>
            </div>
            
            <div className="flex items-center gap-3">
              {/* 三、增加规则 */}
              <Button
                type="button"
                disabled={!schedule.enabled || !schedule.rules_enabled}
                className="flex min-h-9 items-center gap-1 rounded-md border border-cyan-100 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-800 shadow-sm transition-colors duration-200 hover:bg-cyan-100 disabled:pointer-events-none disabled:opacity-50"
                onClick={() =>
                  onChange({
                    ...schedule,
                    rules: [
                      ...schedule.rules,
                      {
                        day: nextRuleDay(schedule.rules),
                        template_key: questionnaireTemplates[0]?.template_key ?? "daily_checkin_simple",
                        message: "今天是第 {day} 天，请完成问卷：\n{questionnaire_link}",
                      },
                    ],
                  })
                }
              >
                <Plus className="h-3.5 w-3.5" />
                增加规则
              </Button>

              <Switch
                checked={schedule.rules_enabled}
                disabled={!schedule.enabled}
                onChange={(checked) =>
                  onChange({
                    ...schedule,
                    rules_enabled: checked,
                  })
                }
              />
            </div>
          </div>
          
          {/* 指定服务日计划规则区域，带开关锁定联动 */}
          <div 
            ref={rulesContainerRef} 
            className={`space-y-3 p-4 transition-all duration-300 ${
              schedule.rules_enabled 
                ? "opacity-100" 
                : "opacity-45 pointer-events-none select-none filter grayscale-[30%]"
            }`}
          >
            {schedule.rules.map((rule, index) => (
              <div
                key={`${rule.day}-${index}`}
                className={`rounded-lg border p-3.5 transition-all duration-300 ${
                  index === schedule.rules.length - 1 && index === highlightedIndex
                    ? "border-cyan-400 bg-cyan-50/40 shadow-sm animate-slide-in animate-highlight-pulse"
                    : "border-slate-200 hover:border-cyan-200 bg-white"
                }`}
              >
                <div className="grid gap-4.5 md:grid-cols-[110px_200px_1fr_auto]">
                  <div className="block">
                    <div className="mb-2 text-xs font-semibold text-slate-700">第几天</div>
                    <Input
                      type="number"
                      min={1}
                      disabled={!schedule.rules_enabled}
                      value={rule.day}
                      onChange={(event) => updateRule(index, { day: Number(event.target.value) || 1 })}
                    />
                  </div>
                  <div className="block">
                    <div className="mb-2 text-xs font-semibold text-slate-700">发送问卷</div>
                    <select
                      className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm focus:border-cyan-700 focus:outline-none disabled:opacity-50"
                      value={rule.template_key}
                      disabled={!schedule.rules_enabled}
                      onChange={(event) => updateRule(index, { template_key: event.target.value })}
                    >
                      {questionnaireTemplates.map((template) => (
                        <option key={template.template_key} value={template.template_key}>
                          {templateLabel(template)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="block">
                    <div className="mb-2 flex items-center justify-between gap-3 text-xs font-semibold text-slate-700">
                      <span>发送话术</span>
                      {/* 五、变量 Tag Panel */}
                      <div className="flex items-center gap-1">
                        {SCHEDULE_MESSAGE_VARIABLES.map((varName) => (
                          <button
                            key={varName}
                            type="button"
                            disabled={!schedule.rules_enabled}
                            onClick={() => {
                              updateRule(index, { message: rule.message + varName });
                            }}
                          className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 font-mono text-xs font-semibold text-cyan-800 shadow-2xs transition-colors hover:bg-cyan-100"
                          >
                            {varName}
                          </button>
                        ))}
                      </div>
                    </div>
                    <Textarea
                      className="min-h-20 text-sm"
                      value={rule.message}
                      disabled={!schedule.rules_enabled}
                      onChange={(event) => updateRule(index, { message: event.target.value })}
                    />
                  </div>
                  <div className="flex items-end">
                    {/* 精致静默的删除按钮，悬停时优雅显现警示色 */}
                    <Button
                      type="button"
                      disabled={!schedule.rules_enabled}
                      className="flex min-h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-500 shadow-2xs transition-colors duration-200 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
                      onClick={() => removeRule(index)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      删除
                    </Button>
                  </div>
                </div>
              </div>
            ))}
            {schedule.rules.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-sm text-slate-400">
                暂无指定推送规则，点击右上角 “增加规则” 按钮以创建。
              </div>
            )}
          </div>
        </div>

        {/* 其他服务日默认发送 */}
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-colors duration-200">
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-100 pb-3">
            <div>
              <h3 className="font-semibold text-slate-950">其他服务日默认发送</h3>
              <p className="mt-1 text-xs text-slate-500">避开上方已指定的特定日期外，其余普通运营服务日默认派发的日常打卡问卷。</p>
            </div>
            <Switch
              checked={schedule.daily_rule.enabled}
              disabled={!schedule.enabled}
              onChange={(checked) =>
                onChange({
                  ...schedule,
                  daily_rule: { ...schedule.daily_rule, enabled: checked },
                })
              }
            />
          </div>
          
          <div className={`transition-all duration-300 space-y-4 ${
            schedule.daily_rule.enabled 
              ? "opacity-100" 
              : "opacity-45 pointer-events-none select-none filter grayscale-[30%]"
          }`}>
            <div className="grid gap-4 md:grid-cols-[200px_1fr]">
              <div className="block">
                <div className="mb-2 text-xs font-semibold text-slate-700">默认问卷</div>
                <select
                  className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm focus:border-cyan-700 focus:outline-none disabled:opacity-50"
                  value={schedule.daily_rule.template_key}
                  disabled={!schedule.daily_rule.enabled}
                  onChange={(event) =>
                    onChange({
                      ...schedule,
                      daily_rule: { ...schedule.daily_rule, template_key: event.target.value },
                    })
                  }
                >
                  {questionnaireTemplates.map((template) => (
                    <option key={template.template_key} value={template.template_key}>
                      {templateLabel(template)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="block">
                <div className="mb-2 flex items-center justify-between gap-3 text-xs font-semibold text-slate-700">
                  <span>默认发送话术</span>
                  {/* 五、变量 Tag Panel */}
                  <div className="flex items-center gap-1">
                    {SCHEDULE_MESSAGE_VARIABLES.map((varName) => (
                      <button
                        key={varName}
                        type="button"
                        disabled={!schedule.daily_rule.enabled}
                        onClick={() => {
                          onChange({
                            ...schedule,
                            daily_rule: {
                              ...schedule.daily_rule,
                              message: schedule.daily_rule.message + varName
                            }
                          });
                        }}
                        className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 font-mono text-xs font-semibold text-cyan-800 shadow-2xs transition-colors hover:bg-cyan-100 disabled:opacity-40"
                      >
                        {varName}
                      </button>
                    ))}
                  </div>
                </div>
                <Textarea
                  className="min-h-20 text-sm"
                  value={schedule.daily_rule.message}
                  disabled={!schedule.daily_rule.enabled}
                  onChange={(event) =>
                    onChange({
                      ...schedule,
                      daily_rule: { ...schedule.daily_rule, message: event.target.value },
                    })
                  }
                />
              </div>
            </div>

            {/* 四、跳过日期的 28天极简微型网格选择器 */}
            <div className="block border-t border-slate-100 pt-3">
              <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3 text-xs font-semibold text-slate-700">
                <span>跳过日期（在选中的天数将不会发送默认打卡问卷）</span>
                <button
                  type="button"
                  disabled={!schedule.daily_rule.enabled}
                  className="group flex items-center gap-1.5 text-xs font-bold text-cyan-700 transition-colors hover:text-cyan-800 hover:underline disabled:pointer-events-none disabled:opacity-50"
                  onClick={() => {
                    const ruleDays = schedule.rules.map(r => r.day).filter(d => d >= 1 && d <= 28);
                    onChange({
                      ...schedule,
                      daily_rule: {
                        ...schedule.daily_rule,
                        skip_days: [...new Set(ruleDays)].sort((a, b) => a - b)
                      }
                    });
                  }}
                >
                  <RefreshCw className="h-3 w-3 transition-transform duration-500 group-hover:rotate-180 shrink-0" />
                  一键同步已配置的指定服务日 (同步后需点击右上角“保存配置”)
                </button>
              </div>
              
              <div className="flex flex-wrap gap-1.5 rounded-lg border border-slate-200 bg-slate-50/40 p-3">
                {Array.from({ length: 28 }).map((_, i) => {
                  const dayNum = i + 1;
                  const isSkipped = schedule.daily_rule.skip_days.includes(dayNum);
                  return (
                    <button
                      key={dayNum}
                      type="button"
                      disabled={!schedule.daily_rule.enabled}
                      onClick={() => {
                        const currentSkipDays = schedule.daily_rule.skip_days;
                        const nextSkipDays = isSkipped
                          ? currentSkipDays.filter(d => d !== dayNum)
                          : [...currentSkipDays, dayNum].sort((a, b) => a - b);
                        onChange({
                          ...schedule,
                          daily_rule: {
                            ...schedule.daily_rule,
                            skip_days: nextSkipDays
                          }
                        });
                      }}
                      className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors duration-200 ${
                        isSkipped
                          ? "bg-cyan-700 text-white shadow-sm ring-2 ring-cyan-500/20 font-bold"
                          : "bg-white text-slate-500 border border-slate-200 hover:bg-slate-100 hover:border-slate-300 hover:text-slate-700 disabled:opacity-30"
                      }`}
                    >
                      {dayNum}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-slate-400">
                高亮数字代表对应服务日将被自动跳过（适用于已在上方设置了专项复盘/标准打卡的特殊日期）。
              </p>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

function normalizeSchedule(value: Record<string, unknown>): ScheduleConfig {
  const dailyRule = value.daily_rule && typeof value.daily_rule === "object"
    ? value.daily_rule as Record<string, unknown>
    : {};
  return {
    enabled: value.enabled !== false,
    send_at: String(value.send_at ?? "09:00").includes(":")
      ? String(value.send_at ?? "09:00").slice(0, 5)
      : String(value.send_at ?? "09:00"),
    timezone: String(value.timezone ?? "Asia/Shanghai"),
    intake_submit_reply:
      value.intake_submit_reply && typeof value.intake_submit_reply === "object"
        ? {
            enabled: (value.intake_submit_reply as Record<string, unknown>).enabled !== false,
            message: String(
              (value.intake_submit_reply as Record<string, unknown>).message
                ?? "问卷已收到，正在生成专属方案。",
            ),
          }
        : {
            enabled: true,
            message:
              "问卷已收到，正在生成专属方案。\n\n我们会基于你的填写结果生成：\n1.《个人能量重启处方单》\n2.《28天执行标准与打卡要求》\n\n生成完成后会第一时间发送给你，请稍等。",
          },
    inactive_followup:
      value.inactive_followup && typeof value.inactive_followup === "object"
        ? {
            enabled: (value.inactive_followup as Record<string, unknown>).enabled !== false,
            hours: Math.max(1, Number((value.inactive_followup as Record<string, unknown>).hours ?? 24) || 24),
            message: String(
              (value.inactive_followup as Record<string, unknown>).message
                ?? "今天先稳住一个小动作就好。你这边如果遇到执行卡点，可以直接发群里，我来帮你一起拆小一点。",
            ),
          }
        : {
            enabled: true,
            hours: 24,
            message: "今天先稳住一个小动作就好。你这边如果遇到执行卡点，可以直接发群里，我来帮你一起拆小一点。",
          },
    rules_enabled: value.rules_enabled !== false,
    rules: Array.isArray(value.rules)
      ? value.rules.map(normalizeRule).filter(Boolean) as ScheduleRule[]
      : [],
    daily_rule: {
      enabled: dailyRule.enabled !== false,
      template_key: String(dailyRule.template_key ?? "daily_checkin_simple"),
      message: String(dailyRule.message ?? "早上好，今天是第 {day} 天。请完成今日简版打卡：\n{questionnaire_link}"),
      skip_days: Array.isArray(dailyRule.skip_days)
        ? dailyRule.skip_days.map((item) => Number(item)).filter((item) => Number.isFinite(item) && item > 0)
        : [1, 3, 7, 14, 21, 28],
    },
  };
}

function normalizeRule(value: unknown): ScheduleRule | null {
  if (!value || typeof value !== "object") return null;
  const rule = value as Record<string, unknown>;
  const day = Number(rule.day);
  if (!Number.isFinite(day) || day <= 0) return null;
  return {
    day,
    template_key: String(rule.template_key ?? "daily_checkin_simple"),
    message: String(rule.message ?? "今天是第 {day} 天，请完成问卷：\n{questionnaire_link}"),
  };
}

function serializeSchedule(schedule: ScheduleConfig) {
  return {
    ...schedule,
    rules: [...schedule.rules].sort((a, b) => a.day - b.day),
    daily_rule: {
      ...schedule.daily_rule,
      skip_days: [...new Set(schedule.daily_rule.skip_days)].sort((a, b) => a - b),
    },
  };
}

function parseDayList(value: string) {
  return value
    .split(/[,\s，、]+/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item) && item > 0);
}

function nextRuleDay(rules: ScheduleRule[]) {
  return Math.max(0, ...rules.map((rule) => Number(rule.day) || 0)) + 1;
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
