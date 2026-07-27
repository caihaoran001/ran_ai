"use client";

import { useEffect } from "react";
import { X, Wrench, CheckCircle2, XCircle, AlertOctagon, Clock, ShieldAlert } from "lucide-react";

export type ToolStat = {
  name: string;
  calls: number;
  successful_calls: number;
  failed_calls: number;
  rejected_calls: number;
  success_rate?: number | null;
  average_latency_ms?: number | null;
  errors: Array<{ key: string; count: number }>;
};

const TOOL_LABELS: Record<string, string> = {
  get_service_progress: "查看服务进度",
  get_recent_checkins: "查看近期打卡",
  get_weight_trend: "查看体重趋势",
  list_delivery_documents: "列出交付文件",
  read_delivery_document: "读取交付文件",
  list_group_files: "列出群文件",
  read_group_file: "读取群文件",
  search_knowledge_base: "检索知识库",
  create_group_reminder: "创建群提醒",
  list_group_reminders: "查看群提醒",
  cancel_group_reminder: "取消群提醒",
  web_search_lifestyle: "生活方式联网检索",
};

const TOOL_DESCRIPTIONS: Record<string, string> = {
  get_service_progress: "查看当前服务单的 28 天日程进度、已完成天数与剩余服务期。",
  get_recent_checkins: "查询用户近期的体测、饮食、运动、睡眠与打卡打标记录。",
  get_weight_trend: "获取用户入营至今的体重与腰围变化数据曲线。",
  list_delivery_documents: "列出服务单关联的所有已生成 H5 / Word 交付文件。",
  read_delivery_document: "读取指定交付文件的 Markdown 正文与结构化内容。",
  list_group_files: "查询服务单社群共享文件夹中的文件列表。",
  read_group_file: "解析并读取社群共享文件的正文与说明。",
  search_knowledge_base: "从健康服务知识库、规则库中检索匹配的标准答复和执行建议。",
  create_group_reminder: "在群内为用户设置定时关怀、打卡提醒或行动通知。",
  list_group_reminders: "查看当前社群中已设置的所有未触发/生效中的定时提醒。",
  cancel_group_reminder: "取消已创建但尚未发送的群定时提醒。",
  web_search_lifestyle: "联网检索生活方式、食材营养或健康知识最新资料。",
};

export function ToolDetailModal({
  tool,
  onClose,
}: {
  tool: ToolStat | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (tool) {
      window.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [tool, onClose]);

  if (!tool) return null;

  const label = TOOL_LABELS[tool.name] || tool.name;
  const desc = TOOL_DESCRIPTIONS[tool.name] || "Agent 可调用的内置业务与数据工具。";
  const successRate = tool.success_rate != null ? tool.success_rate : 0;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fade-in-up">
      {/* Backdrop */}
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />

      {/* Modal Card */}
      <div className="relative z-10 w-full max-w-lg rounded-2xl bg-white shadow-2xl overflow-hidden border border-slate-200">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-500/20 text-cyan-300">
              <Wrench className="h-5 w-5" />
            </span>
            <div>
              <h3 className="text-base font-extrabold">{label}</h3>
              <p className="text-xs text-slate-400 font-mono">{tool.name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          <p className="text-xs leading-5 text-slate-600 rounded-xl bg-slate-50 p-3.5 border border-slate-100">
            {desc}
          </p>

          {/* Success rate bar */}
          <div>
            <div className="flex items-center justify-between text-xs font-bold mb-1.5">
              <span className="text-slate-700">调用成功率</span>
              <span className="text-cyan-700 font-mono">{successRate}%</span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  successRate >= 90
                    ? "bg-emerald-500"
                    : successRate >= 70
                    ? "bg-amber-500"
                    : "bg-rose-500"
                }`}
                style={{ width: `${Math.max(2, successRate)}%` }}
              />
            </div>
          </div>

          {/* Key Metrics Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MetricBox label="调用总次" value={`${tool.calls}次`} icon={Wrench} color="slate" />
            <MetricBox
              label="成功次数"
              value={`${tool.successful_calls}次`}
              icon={CheckCircle2}
              color="emerald"
            />
            <MetricBox
              label="失败次数"
              value={`${tool.failed_calls}次`}
              icon={XCircle}
              color="rose"
            />
            <MetricBox
              label="拒绝/熔断"
              value={`${tool.rejected_calls}次`}
              icon={AlertOctagon}
              color="amber"
            />
          </div>

          {/* Average Latency */}
          <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50/50 p-3.5 text-xs">
            <span className="flex items-center gap-1.5 text-slate-600 font-medium">
              <Clock className="h-4 w-4 text-slate-400" /> 平均单次耗时
            </span>
            <span className="font-mono font-bold text-slate-900">
              {formatLatency(tool.average_latency_ms)}
            </span>
          </div>

          {/* Error Breakdown */}
          {tool.errors.length > 0 ? (
            <div className="rounded-xl border border-rose-100 bg-rose-50/50 p-4">
              <div className="flex items-center gap-1.5 text-xs font-bold text-rose-900 mb-2">
                <ShieldAlert className="h-4 w-4 text-rose-600" /> 主要错误日志统计
              </div>
              <div className="space-y-1.5">
                {tool.errors.map((err, i) => (
                  <div key={i} className="flex justify-between items-center text-xs">
                    <span className="font-mono text-rose-700">{err.key}</span>
                    <span className="font-bold bg-white px-2 py-0.5 rounded border border-rose-200 text-rose-900">
                      {err.count} 次
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-3.5 text-xs text-emerald-800 text-center font-medium">
              ✓ 所选时间段内该工具无报错记录
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-100 bg-slate-50 px-6 py-3.5 text-right">
          <button
            onClick={onClose}
            className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-bold text-white hover:bg-slate-800 transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

function MetricBox({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: string;
  icon: any;
  color: "slate" | "emerald" | "rose" | "amber";
}) {
  const styles = {
    slate: "bg-slate-50 text-slate-900 border-slate-200",
    emerald: "bg-emerald-50 text-emerald-900 border-emerald-200",
    rose: "bg-rose-50 text-rose-900 border-rose-200",
    amber: "bg-amber-50 text-amber-900 border-amber-200",
  };
  return (
    <div className={`rounded-xl border p-2.5 ${styles[color]}`}>
      <div className="flex items-center gap-1 text-[11px] font-semibold opacity-75">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className="mt-1 text-sm font-black tracking-tight">{value}</div>
    </div>
  );
}

function formatLatency(value?: number | null) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} ms`;
}
