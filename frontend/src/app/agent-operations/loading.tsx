import { AIManagementNav } from "@/components/ai-management-nav";

export default function AgentOperationsLoading() {
  return (
    <div
      className="page-stack"
      aria-busy="true"
      aria-label="正在加载运行监控"
    >
      <AIManagementNav active="operations" />
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">AI 助教运行情况</h1>
            <p className="page-subtitle" role="status" aria-live="polite">
              正在加载运行记录和成本数据，请稍候…
            </p>
          </div>
          <div className="h-7 w-40 animate-pulse rounded-full bg-slate-100 motion-reduce:animate-none" />
        </div>
        <div className="mt-5 flex flex-col gap-3 border-t border-slate-100 pt-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="h-12 w-full rounded-xl bg-slate-100 sm:w-96" />
          <div className="h-11 w-52 rounded-xl bg-slate-100" />
        </div>
      </div>

      <div className="space-y-6 animate-pulse motion-reduce:animate-none">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="panel h-28 bg-white" />
          ))}
        </div>

        <div className="panel space-y-5 p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:justify-between">
            <div className="h-11 w-full rounded-xl bg-slate-100 sm:max-w-lg" />
            <div className="h-11 w-full rounded-xl bg-slate-100 sm:w-64" />
          </div>
          {[0, 1, 2, 3].map((item) => (
            <div
              key={item}
              className="h-16 rounded-xl border border-slate-200 bg-slate-50"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
