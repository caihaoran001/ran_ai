import { AIManagementNav } from "@/components/ai-management-nav";

export default function RagEvaluationsLoading() {
  return (
    <div className="page-stack animate-pulse" aria-label="正在加载 AI 回复质量">
      <AIManagementNav active="quality" />
      <div className="h-36 rounded-2xl border border-slate-200 bg-white" />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="h-56 rounded-2xl border border-slate-200 bg-white" />
        <div className="h-56 rounded-2xl border border-slate-200 bg-white" />
      </div>
      <div className="h-72 rounded-2xl border border-slate-200 bg-white" />
    </div>
  );
}
