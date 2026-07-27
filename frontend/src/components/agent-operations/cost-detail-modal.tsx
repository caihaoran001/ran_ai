"use client";

import { useEffect } from "react";
import Link from "next/link";
import { X, Coins, ExternalLink, Cpu, PieChart, User, Calendar } from "lucide-react";
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
    label: "群内回复 Agent",
    description: "回复 Agent 阅读群聊记录、深度思考判断是否回复、检索知识库与使用工具的流程。",
  },
  agent_reminder: {
    label: "定时提醒文案",
    description: "到点后由 ReAct Agent 根据用户近期进度与打卡记录重新组织群提醒内容。",
  },
  archivist_extract: {
    label: "档案官归档",
    description: "读取一批新聊天并一次性抽取小结、阶段成效、稳定画像、FAQ 草稿与案例素材。",
  },
  qwen_vl_translate: {
    label: "图片多模态理解",
    description: "识别分析用户发送的餐食照片、体测报告或包装图，提取结构化文字。",
  },
  intervention_risk: {
    label: "风控与意图识别",
    description: "分析实时入站消息是否存在医疗健康风险、退款/续购意图或转人工信号。",
  },
  intake_doc: {
    label: "入营画像与交付文档",
    description: "读取入营表单与建档数据，由 LLM 渲染定制化的初始交付报告。",
  },
};

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

  const meta = SCENARIO_META[scenario.scenario] || {
    label: scenario.scenario,
    description: "业务模型调用任务",
  };
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
              <p className="text-xs text-slate-400 font-mono">{scenario.scenario}</p>
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
              <div className="text-xs font-semibold text-cyan-800">场景总支出</div>
              <div className="mt-1 text-2xl font-black text-cyan-950">
                ¥{scenario.estimated_cost_yuan.toFixed(4)}
              </div>
              <div className="mt-1 text-xs text-cyan-700 font-medium">
                占全部费用 {share.toFixed(1)}%
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-3.5">
              <div className="text-xs font-semibold text-slate-600">平均单次花费</div>
              <div className="mt-1 text-2xl font-black text-slate-900">
                ¥{avgCost.toFixed(4)}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                成功 {scenario.successful_calls} / {scenario.calls} 次
              </div>
            </div>
          </div>

          {/* Token usage table */}
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h4 className="text-xs font-bold text-slate-900 mb-3 flex items-center gap-1.5">
              <Cpu className="h-4 w-4 text-cyan-700" /> Token 消耗拆解
            </h4>
            <div className="space-y-2 text-xs">
              <div className="flex justify-between py-1 border-b border-slate-100">
                <span className="text-slate-600">输入 Tokens (Prompt)</span>
                <span className="font-mono font-bold text-slate-900">
                  {scenario.prompt_tokens.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-slate-100">
                <span className="text-slate-600">输出 Tokens (Completion)</span>
                <span className="font-mono font-bold text-slate-900">
                  {scenario.completion_tokens.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between py-1 font-bold">
                <span className="text-slate-800">总计 Tokens</span>
                <span className="font-mono text-cyan-800">
                  {scenario.total_tokens.toLocaleString()}
                </span>
              </div>
            </div>
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
                <div className="text-xs text-slate-500 font-semibold">服务单累计 AI 费用</div>
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
                <span className="text-slate-500">请求次数: </span>
                <span className="font-bold text-slate-800">{caseCost.calls} 次</span>
              </div>
              <div>
                <span className="text-slate-500">消耗 Tokens: </span>
                <span className="font-bold text-slate-800">
                  {caseCost.total_tokens.toLocaleString()}
                </span>
              </div>
              {caseCost.last_call_at && (
                <div className="col-span-2 text-slate-500 mt-1">
                  最近一次模型调用：{formatChinaShortDateTime(caseCost.last_call_at)}
                </div>
              )}
            </div>
          </div>

          {/* Scenario breakdown list */}
          <div>
            <h4 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-1.5">
              <PieChart className="h-4 w-4 text-cyan-700" /> 场景消耗分布明细
            </h4>
            <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
              {caseCost.scenarios.map((sc) => {
                const meta = SCENARIO_META[sc.scenario] || { label: sc.scenario };
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
                      <span>{sc.calls} 次请求 · {sc.total_tokens.toLocaleString()} Tokens</span>
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
