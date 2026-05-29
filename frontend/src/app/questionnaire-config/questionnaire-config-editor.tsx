"use client";

import { useMemo, useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";
import { Loader2, CheckCircle2, AlertCircle, Plus, Trash2, Check, RefreshCw } from "lucide-react";

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

function templateLabel(template: { template_key: string; name: string }) {
  return template.name || TEMPLATE_LABELS[template.template_key] || template.template_key;
}

export function QuestionnaireConfigEditor({ config }: { config: Config }) {
  const [templates, setTemplates] = useState(config.templates);
  const [activeKey, setActiveKey] = useState(config.templates[0]?.template_key ?? "intake");
  const [fieldsText, setFieldsText] = useState(() =>
    Object.fromEntries(
      config.templates.map((template) => [
        template.template_key,
        JSON.stringify(template.fields, null, 2),
      ]),
    ),
  );
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
    const nextTemplates: Template[] = [];
    for (const template of templates) {
      let fields: unknown;
      try {
        fields = JSON.parse(fieldsText[template.template_key] ?? "[]");
      } catch {
        const errMsg = `${templateLabel(template)} 的题目 JSON 格式不正确`;
        setMessage(errMsg);
        showToast("error", errMsg);
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 3000);
        return;
      }
      if (!Array.isArray(fields)) {
        const errMsg = `${templateLabel(template)} 的题目根节点必须是数组`;
        setMessage(errMsg);
        showToast("error", errMsg);
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 3000);
        return;
      }
      nextTemplates.push({ ...template, fields: fields as Record<string, unknown>[] });
    }

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

  function updateTemplate(key: string, patch: Partial<Template>) {
    setTemplates((current) =>
      current.map((template) =>
        template.template_key === key ? { ...template, ...patch } : template,
      ),
    );
  }

  if (!activeTemplate) {
    return <section className="rounded-lg border border-zinc-200 bg-white p-4">暂无问卷配置</section>;
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">发送计划</h2>
            <p className="mt-1 text-sm text-zinc-500">
              文案支持 {"{questionnaire_link}"}、{"{day}"}、{"{user_name}"}、{"{case_no}"}、{"{group_id}"}、{"{template_key}"}。
            </p>
          </div>
          <Button
            type="button"
            onClick={save}
            disabled={saveStatus === "saving"}
            className={`transition-all duration-300 active:scale-95 ${
              saveStatus === "success"
                ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                : saveStatus === "error"
                ? "bg-rose-600 hover:bg-rose-500 text-white"
                : "bg-blue-600 hover:bg-blue-500 text-white"
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
              <>保存全部配置</>
            )}
          </Button>
        </div>
        <ScheduleEditor
          schedule={schedule}
          templates={templates}
          onChange={setSchedule}
        />
      </section>

      <section className="rounded-lg border border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 p-4">
          <h2 className="font-semibold">问卷内容</h2>
          <p className="mt-1 text-sm text-zinc-500">每份问卷单独维护，不再把所有题目混在一个编辑框里。</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {templates.map((template) => (
              <button
                key={template.template_key}
                type="button"
                onClick={() => setActiveKey(template.template_key)}
                className={`rounded-md border px-3 py-2 text-sm transition-all duration-200 ${
                  activeKey === template.template_key
                    ? "border-blue-600 bg-blue-600 text-white font-medium shadow-sm shadow-blue-500/10"
                    : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 hover:border-zinc-300"
                }`}
              >
                {templateLabel(template)}
                <span className="ml-2 text-xs opacity-70">{template.fields.length} 项</span>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-4 p-4">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-medium">配置名</div>
              <Input
                value={activeTemplate.name}
                onChange={(event) => updateTemplate(activeTemplate.template_key, { name: event.target.value })}
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-medium">页面标题</div>
              <Input
                value={activeTemplate.title ?? ""}
                onChange={(event) => updateTemplate(activeTemplate.template_key, { title: event.target.value })}
              />
            </label>
          </div>
          <label className="block">
            <div className="mb-2 text-sm font-medium">页面说明</div>
            <Input
              value={activeTemplate.description ?? ""}
              onChange={(event) => updateTemplate(activeTemplate.template_key, { description: event.target.value })}
            />
          </label>
          <label className="block">
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-sm font-medium">题目 JSON</span>
              <span className="text-xs text-zinc-500">template_key: {activeTemplate.template_key}</span>
            </div>
            <Textarea
              className="min-h-[520px] font-mono text-xs"
              value={fieldsText[activeTemplate.template_key] ?? "[]"}
              onChange={(event) =>
                setFieldsText((current) => ({
                  ...current,
                  [activeTemplate.template_key]: event.target.value,
                }))
              }
            />
          </label>
        </div>
      </section>
      
      {/* Floating Toast Notification */}
      {toast && (
        <div className="fixed right-6 top-6 z-50 flex max-w-md items-center gap-3 rounded-xl border border-zinc-200/60 bg-white/80 p-4 shadow-xl backdrop-blur-md animate-toast-in">
          {toast.type === "success" ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0 animate-bounce" />
          ) : (
            <AlertCircle className="h-5 w-5 text-rose-500 shrink-0 animate-pulse" />
          )}
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-semibold text-zinc-900">
              {toast.type === "success" ? "操作成功" : "配置错误"}
            </span>
            <span className="text-xs text-zinc-600 leading-relaxed font-medium">
              {toast.message}
            </span>
          </div>
        </div>
      )}
    </div>
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
      className={`relative inline-flex shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${trackClass} ${
        checked ? "bg-blue-600 hover:bg-blue-500" : "bg-zinc-200 hover:bg-zinc-300"
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
      {/* 🚀 一、全局总控 Banner 开关 */}
      <div className="rounded-xl border border-zinc-200 bg-zinc-50/70 p-4.5 shadow-sm transition-all duration-300">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <h3 className="text-base font-semibold text-zinc-900">自动发送问卷计划总开关</h3>
            <p className="text-xs leading-relaxed text-zinc-500 max-w-2xl">
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

      {/* ⚠️ 当总开关关闭时的视觉提醒 */}
      {!schedule.enabled && (
        <div className="rounded-lg border border-amber-100 bg-amber-50/50 px-4 py-3 text-xs text-amber-700 flex items-center gap-2 animate-fade-in">
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
        <div className="grid gap-4 md:grid-cols-2 rounded-xl border border-zinc-200 bg-white p-4 shadow-2xs">
          <div className="block">
            <div className="mb-2 text-sm font-semibold text-zinc-700">统一发送时间</div>
            <select
              className="flex h-9 w-full rounded-md border border-zinc-200 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-950"
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
            <p className="mt-1.5 text-2xs text-zinc-400">系统定时轮询并自动发卷的时间点。</p>
          </div>
          <div className="block">
            <div className="mb-2 text-sm font-semibold text-zinc-700">系统时区</div>
            <select
              className="flex h-9 w-full rounded-md border border-zinc-200 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-950"
              value={schedule.timezone}
              disabled={!schedule.enabled}
              onChange={(event) => onChange({ ...schedule, timezone: event.target.value })}
            >
              <option value="Asia/Shanghai">北京时间 (Asia/Shanghai)</option>
            </select>
            <p className="mt-1.5 text-2xs text-zinc-400">定时推送时间所采用的时区计算标准。</p>
          </div>
        </div>

        {/* 入营问卷提交后群内回执 */}
        <div className="rounded-xl border border-zinc-200 bg-white shadow-2xs overflow-hidden">
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-zinc-100 bg-zinc-50/10 px-4 py-3">
            <div>
              <h3 className="font-semibold text-zinc-900">入营问卷提交后群内回执</h3>
              <p className="mt-1 text-2xs text-zinc-500">用户提交首份入营问卷后，机器人在群内自动回复一条确认与指引文案。</p>
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
              <div className="mb-2 flex items-center justify-between text-xs font-medium text-zinc-500">
                <span>回执话术</span>
                <div className="flex items-center gap-1">
                  <span className="mr-1.5 text-2xs text-zinc-400">点击追加变量:</span>
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
                      className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 font-mono text-2xs font-semibold text-blue-600 ring-1 ring-blue-500/10 transition-all duration-150 hover:bg-blue-100/70 hover:scale-105 active:scale-95 disabled:opacity-40"
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

        {/* 🚀 二、指定服务日发送（带独立开关） */}
        <div className="rounded-xl border border-zinc-200 bg-white shadow-2xs overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3 bg-zinc-50/20">
            <div>
              <h3 className="font-semibold text-zinc-900">指定服务日发送</h3>
              <p className="mt-1 text-2xs text-zinc-500">在指定的具体服务日（第几天），向群组自动推送特定的打卡/复盘问卷。</p>
            </div>
            
            <div className="flex items-center gap-3">
              {/* 🚀 三、增加规则（带高雅蓝底色） */}
              <Button
                type="button"
                disabled={!schedule.enabled || !schedule.rules_enabled}
                className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-blue-600 bg-blue-50 border border-blue-100 hover:bg-blue-100 hover:text-blue-700 rounded-md transition-all duration-200 active:scale-95 shadow-sm disabled:opacity-50 disabled:pointer-events-none"
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
                    ? "border-blue-400 bg-blue-50/10 shadow-sm animate-slide-in animate-highlight-pulse"
                    : "border-zinc-200 hover:border-zinc-300 bg-white"
                }`}
              >
                <div className="grid gap-4.5 md:grid-cols-[110px_200px_1fr_auto]">
                  <div className="block">
                    <div className="mb-2 text-xs font-semibold text-zinc-700">第几天</div>
                    <Input
                      type="number"
                      min={1}
                      disabled={!schedule.rules_enabled}
                      value={rule.day}
                      onChange={(event) => updateRule(index, { day: Number(event.target.value) || 1 })}
                    />
                  </div>
                  <div className="block">
                    <div className="mb-2 text-xs font-semibold text-zinc-700">发送问卷</div>
                    <select
                      className="h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-zinc-950 disabled:opacity-50"
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
                    <div className="mb-2 text-xs font-semibold text-zinc-700 flex items-center justify-between">
                      <span>发送话术</span>
                      {/* 🚀 五、蓝底变量 Tag Panel */}
                      <div className="flex items-center gap-1">
                        {["{day}", "{questionnaire_link}", "{user_name}"].map((varName) => (
                          <button
                            key={varName}
                            type="button"
                            disabled={!schedule.rules_enabled}
                            onClick={() => {
                              updateRule(index, { message: rule.message + varName });
                            }}
                            className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-3xs font-mono font-semibold text-blue-600 ring-1 ring-blue-500/10 hover:bg-blue-100 hover:scale-105 active:scale-95 transition-all shadow-2xs cursor-pointer"
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
                      className="flex items-center gap-1 border border-zinc-200 bg-white text-zinc-500 hover:text-rose-600 hover:bg-rose-50/50 hover:border-rose-100 shadow-2xs transition-all duration-200 active:scale-[0.97] text-xs px-2.5 py-1.5 rounded-md disabled:opacity-50"
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
              <div className="text-center py-6 text-sm text-zinc-400 border border-dashed border-zinc-200 rounded-lg">
                暂无指定推送规则，点击右上角 “增加规则” 按钮以创建。
              </div>
            )}
          </div>
        </div>

        {/* 其他服务日默认发送 */}
        <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-2xs transition-all duration-300">
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-zinc-100 pb-3">
            <div>
              <h3 className="font-semibold text-zinc-900">其他服务日默认发送</h3>
              <p className="mt-1 text-2xs text-zinc-500">避开上方已指定的特定日期外，其余普通运营服务日默认派发的日常打卡问卷。</p>
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
                <div className="mb-2 text-xs font-semibold text-zinc-700">默认问卷</div>
                <select
                  className="h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-zinc-950 disabled:opacity-50"
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
                <div className="mb-2 text-xs font-semibold text-zinc-700 flex items-center justify-between">
                  <span>默认发送话术</span>
                  {/* 🚀 五、蓝底变量 Tag Panel */}
                  <div className="flex items-center gap-1">
                    {["{day}", "{questionnaire_link}", "{user_name}"].map((varName) => (
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
                        className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-2xs font-mono font-semibold text-blue-600 ring-1 ring-blue-500/10 hover:bg-blue-100 hover:scale-105 active:scale-95 transition-all shadow-2xs cursor-pointer disabled:opacity-40"
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

            {/* 🚀 四、跳过日期的 28天极简微型网格选择器 */}
            <div className="block border-t border-zinc-100 pt-3">
              <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3 text-xs font-semibold text-zinc-700">
                <span>跳过日期（在选中的天数将不会发送默认打卡问卷）</span>
                <button
                  type="button"
                  disabled={!schedule.daily_rule.enabled}
                  className="text-2xs font-bold text-blue-600 hover:text-blue-700 hover:underline transition-all flex items-center gap-1.5 disabled:opacity-50 disabled:pointer-events-none group"
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
                  一键同步已配置的指定服务日 (同步后需点击右上角“保存全部配置”)
                </button>
              </div>
              
              <div className="flex flex-wrap gap-1.5 p-3 rounded-lg border border-zinc-200/60 bg-zinc-50/20">
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
                      className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-all duration-200 active:scale-90 ${
                        isSkipped
                          ? "bg-blue-600 text-white shadow-sm ring-2 ring-blue-500/20 scale-102 font-bold"
                          : "bg-white text-zinc-500 border border-zinc-200 hover:bg-zinc-100 hover:border-zinc-300 hover:text-zinc-700 disabled:opacity-30"
                      }`}
                    >
                      {dayNum}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-2xs text-zinc-400">
                高亮蓝色数字代表对应服务日将被自动跳过（适用于已在上方设置了专项复盘/标准打卡的特殊日期）。
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
