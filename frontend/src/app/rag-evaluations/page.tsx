import { TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import {
  RagEvaluationsClient,
  type RagEvaluationRunList,
} from "@/components/rag-evaluations/rag-evaluations-client";

export default async function RagEvaluationsPage() {
  const data = await api<RagEvaluationRunList>("/rag-evaluations").catch(
    () => null,
  );

  if (!data) {
    return (
      <div className="page-stack">
        <div className="empty-state py-20">
          <TriangleAlert className="mb-2 h-8 w-8 text-amber-500" />
          <p className="text-base font-bold text-slate-800">
            AI 回复质量报告暂时无法读取
          </p>
          <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
            可能是后端正在升级，或报告目录尚未挂载。请稍后刷新。
          </p>
        </div>
      </div>
    );
  }

  return <RagEvaluationsClient initialRuns={data.items} />;
}
