export default function UsersLoading() {
  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div className="space-y-2">
            <div className="h-7 w-32 animate-pulse rounded bg-slate-200" />
            <div className="h-4 w-48 animate-pulse rounded bg-slate-100" />
          </div>
          <div className="h-6 w-20 animate-pulse rounded-full bg-slate-100" />
        </div>
        <div className="mt-4 h-12 w-full animate-pulse rounded-lg bg-slate-100 sm:w-64" />
      </div>

      <div className="h-11 w-full animate-pulse rounded-lg bg-slate-100" />

      <div className="grid gap-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="panel flex flex-col gap-4 p-6">
            <div className="flex items-center gap-4">
              <div className="h-11 w-11 shrink-0 animate-pulse rounded-lg bg-slate-100 sm:h-12 sm:w-12" />
              <div className="space-y-2">
                <div className="h-5 w-32 animate-pulse rounded bg-slate-200" />
                <div className="h-3.5 w-40 animate-pulse rounded bg-slate-100" />
              </div>
            </div>
            <div className="h-20 animate-pulse rounded-lg border border-slate-200 bg-slate-50" />
          </div>
        ))}
      </div>
    </div>
  );
}
