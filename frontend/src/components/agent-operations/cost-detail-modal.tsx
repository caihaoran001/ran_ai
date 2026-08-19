"use client";

import { useEffect } from "react";
import Link from "next/link";
import { X, Coins, ExternalLink, PieChart, User } from "lucide-react";
import { formatChinaShortDateTime } from "@/lib/format";

export type ScenarioCost = {
  scenario: string;
  calls: number;
  successful_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_yuan: number;
};

export type CaseCost = {
  service_case_id: number;
  case_no: string;
  user_name?: string | null;
  calls: number;
  total_tokens: number;
  estimated_cost_yuan: number;
  last_call_at?: string | null;
  scenarios: ScenarioCost[];
};

const SCENARIO_META: Record<string, { label: string; description: string }> = {
  reply_agent: {
    label: "群内回复",
    description: "AI 读群里的消息、判断要不要回、去查资料，然后给用户回复。",
  },
  agent_reminder: {
    label: "定时提醒",
    description: "到点后，AI 根据用户最近的进度和打卡，组织一条群提醒发出去（含现在的打卡追问）。",
  },
  archivist_extract: {
    label: "整理档案",
    description: "AI 隔一段时间把群聊翻一遍，把打卡、成效、用户情况等整理进档案。",
  },
  qwen_vl_translate: {
    label: "看图识别",
    description: "识别用户发的餐食照片、体测报告、产品包装等图片，读出里面的文字信息。",
  },
  message_insight: {
    label: "消息理解与风险识别",
    description: "每条用户消息先过一遍：听懂在说什么，同时看有没有健康风险、退费意向这类要转人工的信号。",
  },
  checkin_extract: {
    label: "打卡整理",
    description: "把用户提交的打卡内容整理成体重、饮食、运动等结构化记录。",
  },
  intake_doc: {
    label: "入营建档与文档",
    description: "根据入营表单和建档信息，生成这位用户专属的初始交付文档。",
  },
  intake_safety_factors: {
    label: "入营风险排查",
    description: "读入营问卷里的疾病、用药、过敏等信息，挑出后续要避开的风险点。",
  },
  stage_report: {
    label: "阶段报告",
    description: "服务过程中按阶段小结这位用户的进展，生成给她看的阶段报告。",
  },
  graduation_report: {
    label: "结营报告",
    description: "28 天服务结束时汇总全程数据和变化，生成结营报告。",
  },
  churn_recall: {
    label: "沉默用户唤回",
    description: "用户长时间不说话、不打卡时，AI 组织一条唤回消息把人拉回来。",
  },
  knowledge_dedupe: {
    label: "知识库查重",
    description: "新知识入库前先比对已有条目，避免重复内容堆在知识库里。",
  },
  knowledge_evolution_case: {
    label: "优秀案例沉淀",
    description: "把服务得好的真实案例整理成可复用的案例素材，供 AI 后续引用。",
  },
  population_insight: {
    label: "全员画像洞察",
    description: "把所有在服用户的画像汇到一起，看整体人群特征和共性问题。",
  },
  diagnostics_probe: {
    label: "系统自检",
    description: "后台定期发一条极短的测试请求，确认大模型通道还连得上。",
  },
  diag: {
    label: "系统自检（旧版）",
    description: "早期的大模型连通性自检，现已由「系统自检」取代，这里只剩历史花费。",
  },
  ai_reply: {
    label: "群内回复（旧版）",
    description: "早期版本的群内回复，现已由「群内回复」取代，这里只剩历史花费。",
  },
  intervention_risk: {
    label: "风险识别（旧版）",
    description: "早期单独跑的一次风险判断，现已并进「消息理解与风险识别」，这里只剩历史花费。",
  },
  outcome_extract: {
    label: "成效提取（旧版）",
    description: "早期逐条实时提取服务成效，现已并进「整理档案」，这里只剩历史花费。",
  },
  profile_refresh: {
    label: "用户画像更新（旧版）",
    description: "早期单独更新用户画像，现已并进「整理档案」，这里只剩历史花费。",
  },
  checkin_followup: {
    label: "打卡追问（旧版）",
    description: "早期单独跑的打卡追问，现在这部分花费记在「定时提醒」里，这里只剩历史花费。",
  },
  generic: {
    label: "其它 AI 调用",
    description: "没有归到具体功能的零散调用。",
  },
};

const UNKNOWN_SCENARIO_META = {
  label: "其它 AI 功能",
  description: "AI 的一项功能。",
};

export function scenarioMeta(scenario: string) {
  return SCENARIO_META[scenario] ?? UNKNOWN_SCENARIO_META;
}

export function scenarioLabel(scenario: string) {
  return scenarioMeta(scenario).label;
}

export function ScenarioCostModal({
  scenario,
  totalCost,
  onClose,
}: {
  scenario: ScenarioCost | null;
  totalCost: number;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (scenario) {
      window.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [scenario, onClose]);

  if (!scenario) return null;

  const meta = scenarioMeta(scenario.scenario);
  const share = totalCost ? (scenario.estimated_cost_yuan / totalCost) * 100 : 0;
  const avgCost = scenario.calls ? scenario.estimated_cost_yuan / scenario.calls : 0;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fade-in-up">
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 w-full max-w-lg rounded-2xl bg-white shadow-2xl overflow-hidden border border-slate-200">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-500/20 text-cyan-300">
              <Coins className="h-5 w-5" />
            </span>
            <div>
              <h3 className="text-base font-extrabold">{meta.label}</h3>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <p className="text-xs leading-5 text-slate-600 rounded-xl bg-slate-50 p-3.5 border border-slate-100">
            {meta.description}
          </p>

          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-cyan-100 bg-cyan-50/50 p-3.5">
              <div className="text-xs font-semibold text-cyan-800">这项功能花了</div>
              <div className="mt-1 text-2xl font-black text-cyan-950">
                ¥{scenario.estimated_cost_yuan.toFixed(4)}
              </div>
              <div className="mt-1 text-xs text-cyan-700 font-medium">
                占全部费用 {share.toFixed(1)}%
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-3.5">
              <div className="text-xs font-semibold text-slate-600">平均每次</div>
              <div className="mt-1 text-2xl font-black text-slate-900">
                ¥{avgCost.toFixed(4)}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                成功 {scenario.successful_calls} / {scenario.calls} 次
              </div>
            </div>
          </div>

          {/* Share bar */}
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between text-xs font-bold mb-2">
              <span className="text-slate-700">在总花费里的占比</span>
              <span className="text-cyan-700">{share.toFixed(1)}%</span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-cyan-600"
                style={{ width: `${Math.max(2, share)}%` }}
              />
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500">
              这段时间共调用了 {scenario.calls.toLocaleString()} 次。
            </p>
          </div>
        </div>

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

export function CaseCostDrawer({
  caseCost,
  onClose,
}: {
  caseCost: CaseCost | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (caseCost) {
      window.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [caseCost, onClose]);

  if (!caseCost) return null;

  return (
    <div className="fixed inset-0 z-[80] flex justify-end overflow-hidden bg-slate-900/60 backdrop-blur-sm animate-fade-in-up">
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 flex w-full max-w-lg flex-col bg-white shadow-2xl transition-transform duration-300">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-500/20 text-cyan-300">
              <User className="h-5 w-5" />
            </span>
            <div>
              <h3 className="text-base font-extrabold">
                {caseCost.user_name || "未命名用户"} 的 AI 消耗
              </h3>
              <p className="text-xs text-slate-400 font-mono">{caseCost.case_no}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Summary Box */}
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
            <div className="flex justify-between items-center">
              <div>
                <div className="text-xs text-slate-500 font-semibold">这位用户累计花费</div>
                <div className="text-3xl font-black text-slate-900 mt-1">
                  ¥{caseCost.estimated_cost_yuan.toFixed(4)}
                </div>
              </div>
              <Link
                href={`/cases/${caseCost.service_case_id}`}
                target="_blank"
                className="inline-flex items-center gap-1 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-bold text-white hover:bg-cyan-700 transition-colors shadow-sm"
              >
                前往服务单 <ExternalLink className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="grid grid-cols-2 gap-2 border-t border-slate-200 pt-3 text-xs">
              <div>
                <span className="text-slate-500">调用次数：</span>
                <span className="font-bold text-slate-800">{caseCost.calls} 次</span>
              </div>
              {caseCost.last_call_at && (
                <div>
                  <span className="text-slate-500">最近一次：</span>
                  <span className="font-bold text-slate-800">
                    {formatChinaShortDateTime(caseCost.last_call_at)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Scenario breakdown list */}
          <div>
            <h4 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-1.5">
              <PieChart className="h-4 w-4 text-cyan-700" /> 各项功能分别花了多少
            </h4>
            <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
              {caseCost.scenarios.map((sc) => {
                const meta = scenarioMeta(sc.scenario);
                const scenarioShare = caseCost.estimated_cost_yuan
                  ? (sc.estimated_cost_yuan / caseCost.estimated_cost_yuan) * 100
                  : 0;
                return (
                  <div key={sc.scenario} className="p-3.5 space-y-1.5">
                    <div className="flex justify-between items-center text-xs">
                      <span className="font-bold text-slate-900">{meta.label}</span>
                      <span className="font-bold font-mono text-cyan-800">
                        ¥{sc.estimated_cost_yuan.toFixed(4)}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-[11px] text-slate-500">
                      <span>{sc.calls} 次</span>
                      <span>占比 {scenarioShare.toFixed(1)}%</span>
                    </div>
                    <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden mt-1">
                      <div
                        className="h-full bg-cyan-600 rounded-full"
                        style={{ width: `${Math.max(3, scenarioShare)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 text-right">
          <button
            onClick={onClose}
            className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-bold text-white hover:bg-slate-800 transition-colors"
          >
            关闭面板
          </button>
        </div>
      </div>
    </div>
  );
}
