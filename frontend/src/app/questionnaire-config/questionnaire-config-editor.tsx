"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";

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
  rules: ScheduleRule[];
  daily_rule: DailyRule;
};

const TEMPLATE_LABELS: Record<string, string> = {
  intake: "入营问卷",
  daily_checkin_standard: "打卡标准版",
  daily_checkin_simple: "打卡简版",
  stage_review: "阶段复盘",
};

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

  const activeTemplate = useMemo(
    () => templates.find((template) => template.template_key === activeKey) ?? templates[0],
    [activeKey, templates],
  );

  async function save() {
    setMessage("");
    const nextTemplates: Template[] = [];
    for (const template of templates) {
      let fields: unknown;
      try {
        fields = JSON.parse(fieldsText[template.template_key] ?? "[]");
      } catch {
        setMessage(`${templateLabel(template)} 的题目 JSON 格式不正确`);
        return;
      }
      if (!Array.isArray(fields)) {
        setMessage(`${templateLabel(template)} 的题目根节点必须是数组`);
        return;
      }
      nextTemplates.push({ ...template, fields: fields as Record<string, unknown>[] });
    }

    const response = await fetch(`${API_BASE}/questionnaire-template/config`, {
      method: "PUT",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({ templates: nextTemplates, schedule: serializeSchedule(schedule) }),
    });
    setMessage(response.ok ? "已保存" : await response.text());
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
          <Button type="button" onClick={save}>保存全部配置</Button>
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
                className={`rounded-md border px-3 py-2 text-sm ${
                  activeKey === template.template_key
                    ? "border-zinc-900 bg-zinc-900 text-white"
                    : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
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
      {message && <p className="text-sm text-zinc-600">{message}</p>}
    </div>
  );
}

function templateLabel(template: Template) {
  return TEMPLATE_LABELS[template.template_key] ?? template.name ?? template.template_key;
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
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <div className="flex flex-col justify-center rounded-md border border-zinc-200 px-4 py-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">定时发卷机器人</span>
            <label className="relative inline-flex cursor-pointer items-center">
              <input
                type="checkbox"
                checked={schedule.enabled}
                onChange={(event) => onChange({ ...schedule, enabled: event.target.checked })}
                className="peer sr-only"
              />
              <div className="peer h-6 w-11 rounded-full bg-zinc-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300" />
            </label>
          </div>
        </div>
        <label className="block">
          <div className="mb-2 text-sm font-medium">发送时间</div>
          <select
            className="flex h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-950"
            value={schedule.send_at}
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
        </label>
        <label className="block">
          <div className="mb-2 text-sm font-medium">时区</div>
          <Input
            value={schedule.timezone}
            onChange={(event) => onChange({ ...schedule, timezone: event.target.value })}
          />
        </label>
      </div>

      <div className="rounded-lg border border-zinc-200">
        <div className="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3">
          <div>
            <h3 className="font-medium">指定服务日发送</h3>
            <p className="mt-1 text-xs text-zinc-500">例如第 1 天发标准打卡，第 7 天发阶段复盘。</p>
          </div>
          <Button
            type="button"
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
            增加规则
          </Button>
        </div>
        <div className="space-y-3 p-4">
          {schedule.rules.map((rule, index) => (
            <div key={`${rule.day}-${index}`} className="rounded-md border border-zinc-200 p-3">
              <div className="grid gap-3 md:grid-cols-[120px_220px_1fr_auto]">
                <label className="block">
                  <div className="mb-2 text-xs font-medium text-zinc-500">第几天</div>
                  <Input
                    type="number"
                    min={1}
                    value={rule.day}
                    onChange={(event) => updateRule(index, { day: Number(event.target.value) || 1 })}
                  />
                </label>
                <label className="block">
                  <div className="mb-2 text-xs font-medium text-zinc-500">发送问卷</div>
                  <select
                    className="h-9 w-full rounded-md border border-zinc-300 bg-white px-3 text-sm"
                    value={rule.template_key}
                    onChange={(event) => updateRule(index, { template_key: event.target.value })}
                  >
                    {questionnaireTemplates.map((template) => (
                      <option key={template.template_key} value={template.template_key}>
                        {templateLabel(template)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <div className="mb-2 text-xs font-medium text-zinc-500">发送话术</div>
                  <Textarea
                    className="min-h-20 text-sm"
                    value={rule.message}
                    onChange={(event) => updateRule(index, { message: event.target.value })}
                  />
                </label>
                <div className="flex items-end">
                  <Button
                    type="button"
                    className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
                    onClick={() => removeRule(index)}
                  >
                    删除
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-zinc-200 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="font-medium">其他服务日默认发送</h3>
            <p className="mt-1 text-xs text-zinc-500">避开上方指定日期后，普通服务日默认发送这份问卷。</p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={schedule.daily_rule.enabled}
              onChange={(event) =>
                onChange({
                  ...schedule,
                  daily_rule: { ...schedule.daily_rule, enabled: event.target.checked },
                })
              }
            />
            启用默认发送
          </label>
        </div>
        <div className="grid gap-3 md:grid-cols-[220px_1fr]">
          <label className="block">
            <div className="mb-2 text-xs font-medium text-zinc-500">默认问卷</div>
            <select
              className="h-9 w-full rounded-md border border-zinc-300 bg-white px-3 text-sm"
              value={schedule.daily_rule.template_key}
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
          </label>
          <label className="block">
            <div className="mb-2 text-xs font-medium text-zinc-500">默认发送话术</div>
            <Textarea
              className="min-h-20 text-sm"
              value={schedule.daily_rule.message}
              onChange={(event) =>
                onChange({
                  ...schedule,
                  daily_rule: { ...schedule.daily_rule, message: event.target.value },
                })
              }
            />
          </label>
        </div>
        <label className="mt-3 block">
          <div className="mb-2 text-xs font-medium text-zinc-500">跳过日期</div>
          <Input
            value={schedule.daily_rule.skip_days.join(",")}
            onChange={(event) =>
              onChange({
                ...schedule,
                daily_rule: {
                  ...schedule.daily_rule,
                  skip_days: parseDayList(event.target.value),
                },
              })
            }
            placeholder="例如：1,3,7,14,21,28"
          />
        </label>
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
