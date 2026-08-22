"use client";

import { useState } from "react";
import { Archive, BookOpenCheck, Bot } from "lucide-react";
import {
  AgentEvaluationsClient,
  type AgentEvaluationSummary,
} from "@/components/agent-evaluations/agent-evaluations-client";
import {
  RagEvaluationsClient,
  type AnswerRelevancyCalibrationReport,
  type FaithfulnessCalibrationReport,
  type RagEvaluationSummary,
} from "@/components/rag-evaluations/rag-evaluations-client";
import {
  ReplyAgentEvaluationsClient,
  type ReplyAgentEvaluationSummary,
} from "@/components/reply-agent-evaluations/reply-agent-evaluations-client";

type QualityTab = "unified" | "rag" | "agent";

export function ReplyQualityClient({
  replyAgentRuns,
  ragRuns,
  agentRuns,
  initialCalibration,
  initialFaithfulnessCalibration,
}: {
  replyAgentRuns: ReplyAgentEvaluationSummary[] | null;
  ragRuns: RagEvaluationSummary[] | null;
  agentRuns: AgentEvaluationSummary[] | null;
  initialCalibration: AnswerRelevancyCalibrationReport | null;
  initialFaithfulnessCalibration: FaithfulnessCalibrationReport | null;
}) {
  const [tab, setTab] = useState<QualityTab>(
    replyAgentRuns ? "unified" : ragRuns ? "rag" : "agent",
  );

  return (
    <div className="page-stack">
      <nav
        aria-label="回复质量评测类型"
        className="panel flex min-w-0 flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between sm:px-4"
      >
        <div className="min-w-0 px-1">
          <p className="text-sm font-extrabold text-slate-900">回复质量评测</p>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">
            一套题库覆盖回复、知识检索、工具任务和安全边界；旧批次保留供历史对照。
          </p>
        </div>
        <div className="segmented-control grid-cols-3" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "unified"}
            onClick={() => setTab("unified")}
            className={`segmented-item flex items-center justify-center gap-2 whitespace-nowrap px-2 text-xs sm:px-3.5 sm:text-sm ${tab === "unified" ? "segmented-item-active" : ""}`}
          >
            <Bot className="h-4 w-4" />
            统一回复 Agent
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "rag"}
            onClick={() => setTab("rag")}
            className={`segmented-item flex items-center justify-center gap-2 whitespace-nowrap px-2 text-xs sm:px-3.5 sm:text-sm ${tab === "rag" ? "segmented-item-active" : ""}`}
          >
            <BookOpenCheck className="h-4 w-4" />
            历史 RAG
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "agent"}
            onClick={() => setTab("agent")}
            className={`segmented-item flex items-center justify-center gap-2 whitespace-nowrap px-2 text-xs sm:px-3.5 sm:text-sm ${tab === "agent" ? "segmented-item-active" : ""}`}
          >
            <Archive className="h-4 w-4" />
            历史 Agent
          </button>
        </div>
      </nav>

      <div role="tabpanel">
        {tab === "unified" ? (
          replyAgentRuns ? (
            <ReplyAgentEvaluationsClient initialRuns={replyAgentRuns} />
          ) : (
            <UnavailableReport kind="统一回复 Agent" />
          )
        ) : tab === "rag" ? (
          ragRuns ? (
            <RagEvaluationsClient
              initialRuns={ragRuns}
              initialCalibration={initialCalibration}
              initialFaithfulnessCalibration={initialFaithfulnessCalibration}
            />
          ) : (
            <UnavailableReport kind="知识问答" />
          )
        ) : agentRuns ? (
          <AgentEvaluationsClient initialRuns={agentRuns} />
        ) : (
          <UnavailableReport kind="Agent 任务" />
        )}
      </div>
    </div>
  );
}

function UnavailableReport({ kind }: { kind: string }) {
  return (
    <div className="empty-state py-20">
      <p className="text-base font-bold text-slate-800">{kind}报告暂时无法读取</p>
      <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
        可能是后端正在升级，或报告目录尚未挂载。另一个评测页签仍可正常查看。
      </p>
    </div>
  );
}
