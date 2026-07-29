export default function DocumentsLoading() {
  return (
    <div
      className="page-stack"
      aria-busy="true"
      aria-label="正在加载审核控制台"
    >
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">审核控制台</h1>
            <p className="page-subtitle" role="status" aria-live="polite">
              正在加载审核队列，请稍候…
            </p>
          </div>
          <div className="h-8 w-24 animate-pulse rounded-lg bg-slate-100 motion-reduce:animate-none" />
        </div>
      </div>

      <div className="space-y-6 animate-pulse motion-reduce:animate-none">
        <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
          <div className="grid divide-y divide-slate-800 md:grid-cols-[1.2fr_2fr] md:divide-x md:divide-y-0">
            <div className="space-y-3 p-5">
              <div className="h-5 w-32 rounded bg-slate-700" />
              <div className="h-4 w-full max-w-sm rounded bg-slate-800" />
            </div>
            <div className="grid grid-cols-3 divide-x divide-slate-800">
              {[0, 1, 2].map((item) => (
                <div key={item} className="space-y-3 p-4 sm:p-5">
                  <div className="h-8 w-10 rounded bg-slate-700" />
                  <div className="h-3 w-16 rounded bg-slate-800" />
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          {[0, 1].map((item) => (
            <div key={item} className="panel h-28 bg-white" />
          ))}
        </div>

        <div className="panel space-y-4 p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:justify-between">
            <div className="h-11 w-full rounded-xl bg-slate-100 sm:max-w-lg" />
            <div className="h-11 w-full rounded-xl bg-slate-100 sm:w-72" />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {[0, 1, 2, 3].map((item) => (
              <div
                key={item}
                className="h-32 rounded-xl border border-slate-200 bg-slate-50"
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
