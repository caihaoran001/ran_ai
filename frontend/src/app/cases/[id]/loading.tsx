export default function CaseDetailLoading() {
  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div className="min-w-0 space-y-3">
            <div className="flex items-center gap-3">
              <div className="h-7 w-48 animate-pulse rounded bg-slate-200" />
              <div className="h-6 w-16 animate-pulse rounded-full bg-slate-100" />
            </div>
            <div className="h-4 w-64 animate-pulse rounded bg-slate-100" />
          </div>
          <div className="flex gap-2">
            <div className="h-9 w-24 animate-pulse rounded-lg bg-slate-100" />
            <div className="h-9 w-24 animate-pulse rounded-lg bg-slate-100" />
          </div>
        </div>
      </div>

      <div className="h-14 w-full animate-pulse rounded-xl bg-slate-100" />

      <div className="grid gap-4 xl:grid-cols-12">
        <div className="h-24 animate-pulse rounded-xl bg-slate-100 xl:col-span-6" />
        <div className="h-24 animate-pulse rounded-xl bg-slate-100 xl:col-span-2" />
        <div className="h-24 animate-pulse rounded-xl bg-slate-100 xl:col-span-2" />
        <div className="h-24 animate-pulse rounded-xl bg-slate-100 xl:col-span-2" />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-slate-100" />
        ))}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="panel p-6">
            <div className="h-5 w-32 animate-pulse rounded bg-slate-200" />
            <div className="mt-4 h-40 animate-pulse rounded-lg border border-slate-200 bg-slate-50" />
          </div>
        ))}
      </div>
    </div>
  );
}
