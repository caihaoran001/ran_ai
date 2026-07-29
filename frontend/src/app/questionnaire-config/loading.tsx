export default function QuestionnaireConfigLoading() {
  return (
    <div
      className="page-stack"
      aria-busy="true"
      aria-label="正在加载运营规则配置"
    >
      <div>
        <h1 className="page-heading">运营规则配置</h1>
        <p className="page-subtitle" role="status" aria-live="polite">
          正在加载运营规则，请稍候…
        </p>
      </div>

      <div className="space-y-6 animate-pulse motion-reduce:animate-none">
        <div className="panel space-y-5 p-5">
          <div className="h-6 w-40 rounded bg-slate-200" />
          <div className="grid gap-4 lg:grid-cols-2">
            {[0, 1, 2, 3].map((item) => (
              <div
                key={item}
                className="h-24 rounded-xl border border-slate-200 bg-slate-50"
              />
            ))}
          </div>
          <div className="h-11 w-32 rounded-lg bg-slate-100" />
        </div>

        {[0, 1, 2].map((item) => (
          <div key={item} className="panel space-y-4 p-5">
            <div className="h-5 w-36 rounded bg-slate-200" />
            <div className="h-11 w-full rounded-lg bg-slate-100" />
            <div className="h-20 w-full rounded-lg bg-slate-50" />
          </div>
        ))}
      </div>
    </div>
  );
}
