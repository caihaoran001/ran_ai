import { api } from "@/lib/api";
import {
  type AnswerRelevancyCalibrationReport,
  type FaithfulnessCalibrationReport,
  type RagEvaluationRunList,
} from "@/components/rag-evaluations/rag-evaluations-client";
import { type AgentEvaluationRunList } from "@/components/agent-evaluations/agent-evaluations-client";
import { type ReplyAgentEvaluationRunList } from "@/components/reply-agent-evaluations/reply-agent-evaluations-client";
import { type ArchivistEvaluationRunList } from "@/components/archivist-evaluations/archivist-evaluations-client";
import { ReplyQualityClient } from "@/components/reply-quality/reply-quality-client";

export default async function RagEvaluationsPage() {
  const [replyAgentData, data, agentData, archivistData, calibration, faithfulnessCalibration] = await Promise.all([
    api<ReplyAgentEvaluationRunList>("/reply-agent-evaluations").catch(() => null),
    api<RagEvaluationRunList>("/rag-evaluations").catch(() => null),
    api<AgentEvaluationRunList>("/agent-evaluations").catch(() => null),
    api<ArchivistEvaluationRunList>("/archivist-evaluations").catch(() => null),
    api<AnswerRelevancyCalibrationReport | null>(
      "/rag-evaluations/calibrations/answer-relevancy",
    ).catch(() => null),
    api<FaithfulnessCalibrationReport | null>(
      "/rag-evaluations/calibrations/faithfulness",
    ).catch(() => null),
  ]);

  return (
    <ReplyQualityClient
      replyAgentRuns={replyAgentData?.items ?? null}
      ragRuns={data?.items ?? null}
      agentRuns={agentData?.items ?? null}
      archivistRuns={archivistData?.items ?? null}
      initialCalibration={calibration}
      initialFaithfulnessCalibration={faithfulnessCalibration}
    />
  );
}
