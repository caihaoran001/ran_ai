export default function DashboardLoading() {
  return (
    <div
      className="page-stack"
      aria-busy="true"
      aria-label="正在加载数据看板"
    >
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">数据看板</h1>
            <p className="page-subtitle" role="status" aria-live="polite">
              正在汇总运营数据，请稍候…
            </p>
          </div>
          <div className="h-8 w-28 animate-pulse rounded-lg bg-slate-100 motion-reduce:animate-none" />
        </div>
      </div>

      <div className="space-y-6 animate-pulse motion-reduce:animate-none">
        <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
          <div className="grid lg:grid-cols-[1.2fr_2fr]">
            <div className="space-y-4 border-b border-slate-800 p-5 lg:border-b-0 lg:border-r lg:p-6">
              <div className="h-4 w-32 rounded bg-slate-700" />
              <div className="h-10 w-20 rounded bg-slate-700" />
              <div className="h-4 w-full max-w-xs rounded bg-slate-800" />
            </div>
            <div className="grid grid-cols-2 divide-x divide-y divide-slate-800 sm:grid-cols-4 sm:divide-y-0">
              {[0, 1, 2, 3].map((item) => (
                <div key={item} className="space-y-3 p-5">
                  <div className="h-8 w-12 rounded bg-slate-700" />
                  <div className="h-3 w-16 rounded bg-slate-800" />
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="panel h-32 bg-white" />
          ))}
        </div>

        <div className="panel space-y-4 p-5">
          <div className="h-5 w-36 rounded bg-slate-200" />
          <div className="grid gap-4 lg:grid-cols-3">
            {[0, 1, 2].map((item) => (
              <div
                key={item}
                className="h-28 rounded-xl border border-slate-200 bg-slate-50"
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
