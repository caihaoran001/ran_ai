import { api } from "@/lib/api";
import {
  type AnswerRelevancyCalibrationReport,
  type FaithfulnessCalibrationReport,
  type RagEvaluationRunList,
} from "@/components/rag-evaluations/rag-evaluations-client";
import { type AgentEvaluationRunList } from "@/components/agent-evaluations/agent-evaluations-client";
import { ReplyQualityClient } from "@/components/reply-quality/reply-quality-client";

export default async function RagEvaluationsPage() {
  const [data, agentData, calibration, faithfulnessCalibration] = await Promise.all([
    api<RagEvaluationRunList>("/rag-evaluations").catch(() => null),
    api<AgentEvaluationRunList>("/agent-evaluations").catch(() => null),
    api<AnswerRelevancyCalibrationReport | null>(
      "/rag-evaluations/calibrations/answer-relevancy",
    ).catch(() => null),
    api<FaithfulnessCalibrationReport | null>(
      "/rag-evaluations/calibrations/faithfulness",
    ).catch(() => null),
  ]);

  return (
    <ReplyQualityClient
      ragRuns={data?.items ?? null}
      agentRuns={agentData?.items ?? null}
      initialCalibration={calibration}
      initialFaithfulnessCalibration={faithfulnessCalibration}
    />
  );
}
