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
  search_success_case: "查找优秀案例",
  skill: "调取话术方法",
  read_skill_file: "读取话术材料",
  create_group_reminder: "创建群提醒",
  list_group_reminders: "查看群提醒",
  cancel_group_reminder: "取消群提醒",
  web_search_lifestyle: "上网查资料",
  record_checkin: "代记一次打卡",
  get_checkin_checklist: "查看打卡项目",
  set_checkin_checklist: "调整打卡项目",
  restore_default_checkin_checklist: "恢复默认打卡项目",
  get_checkin_followup_preferences: "查看追问偏好",
  update_checkin_followup_preferences: "调整追问偏好",
  get_checkin_reward_status: "查看打卡奖励进度",
  manage_reward_shipping: "处理奖励寄送",
  continue_service: "延长服务期",
  end_continued_service: "结束延长服务",
  set_service_start_date: "调整服务开始日期",
};

export function toolLabel(name: string) {
  return TOOL_LABELS[name] ?? "其它小工具";
}

const TOOL_DESCRIPTIONS: Record<string, string> = {
  get_service_progress: "看这位用户的服务进行到第几天、还剩多少天。",
  get_recent_checkins: "看用户最近的体测、饮食、运动、睡眠等打卡记录。",
  get_weight_trend: "看用户从入营到现在的体重、腰围变化。",
  list_delivery_documents: "列出发给这位用户的所有交付文件。",
  read_delivery_document: "读取某份交付文件的内容。",
  list_group_files: "看群共享文件夹里有哪些文件。",
  read_group_file: "读取某个群文件的内容。",
  search_knowledge_base: "从知识库里找出对应的标准答复和建议。",
  search_success_case: "用户想放弃或受挫时，找一个情况相近、做成了的真实案例来鼓励她。",
  skill: "遇到特定场景时，调出对应的应对方法和话术要点。",
  read_skill_file: "打开话术方法里引用的附件，看更细的说明。",
  create_group_reminder: "给用户设置定时关怀、打卡提醒等。",
  list_group_reminders: "看这个群已经设置了哪些定时提醒。",
  cancel_group_reminder: "取消一个还没发出的定时提醒。",
  web_search_lifestyle: "上网查生活方式、食材营养等最新资料。",
  record_checkin: "用户在群里直接报了数据时，替她记一条打卡。",
  get_checkin_checklist: "看这位用户当前每天要打哪几项。",
  set_checkin_checklist: "按用户情况增减她每天要打的项目。",
  restore_default_checkin_checklist: "把打卡项目恢复成默认的那几项。",
  get_checkin_followup_preferences: "看这位用户设置了哪些不用再追问的项目。",
  update_checkin_followup_preferences: "用户说某项不用再问了，就在这里关掉。",
  get_checkin_reward_status: "看用户的打卡奖励攒到哪一步、有没有达标。",
  manage_reward_shipping: "登记奖励的收货信息、推进寄送。",
  continue_service: "28 天结束后继续服务这位用户时，延长服务期。",
  end_continued_service: "结束之前延长出来的服务期。",
  set_service_start_date: "开营日期有出入时，更正服务的开始日期。",
};

// 工具报错原因 → 一句人话，未知的一律归为「其它错误」。
const ERROR_LABELS: Record<string, string> = {
  timeout: "响应超时",
  not_found: "没找到数据",
  invalid_argument: "参数不对",
  permission_denied: "没有权限",
  rate_limited: "调用太频繁被限流",
  network_error: "网络问题",
  internal_error: "内部错误",
  parse_error: "结果解析失败",
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

  const label = toolLabel(tool.name);
  const desc = TOOL_DESCRIPTIONS[tool.name] || "AI 回复时会用到的一个小工具。";
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
              <span className="text-slate-700">成功率</span>
              <span className="text-cyan-700">{successRate}%</span>
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
            <MetricBox label="用了多少次" value={`${tool.calls}次`} icon={Wrench} color="slate" />
            <MetricBox
              label="成功"
              value={`${tool.successful_calls}次`}
              icon={CheckCircle2}
              color="emerald"
            />
            <MetricBox
              label="失败"
              value={`${tool.failed_calls}次`}
              icon={XCircle}
              color="rose"
            />
            <MetricBox
              label="被拦下"
              value={`${tool.rejected_calls}次`}
              icon={AlertOctagon}
              color="amber"
            />
          </div>

          {/* Average Latency */}
          <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50/50 p-3.5 text-xs">
            <span className="flex items-center gap-1.5 text-slate-600 font-medium">
              <Clock className="h-4 w-4 text-slate-400" /> 平均每次耗时
            </span>
            <span className="font-bold text-slate-900">
              {formatLatency(tool.average_latency_ms)}
            </span>
          </div>

          {/* Error Breakdown */}
          {tool.errors.length > 0 ? (
            <div className="rounded-xl border border-rose-100 bg-rose-50/50 p-4">
              <div className="flex items-center gap-1.5 text-xs font-bold text-rose-900 mb-2">
                <ShieldAlert className="h-4 w-4 text-rose-600" /> 出错的原因
              </div>
              <div className="space-y-1.5">
                {tool.errors.map((err, i) => (
                  <div key={i} className="flex justify-between items-center text-xs">
                    <span className="text-rose-700">{ERROR_LABELS[err.key] ?? "其它错误"}</span>
                    <span className="font-bold bg-white px-2 py-0.5 rounded border border-rose-200 text-rose-900">
                      {err.count} 次
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-3.5 text-xs text-emerald-800 text-center font-medium">
              ✓ 这段时间没有出错
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
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}
