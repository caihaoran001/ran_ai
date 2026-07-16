export default function UserSegmentsLoading() {
  return (
    <div className="page-stack" aria-busy="true" aria-label="正在加载群体画像">
      <div className="page-header">
        <div className="flex items-start gap-3">
          <div className="h-10 w-10 shrink-0 rounded-xl bg-slate-100" />
          <div className="min-w-0 flex-1 space-y-3 py-1">
            <div className="h-6 w-32 rounded-md bg-slate-100" />
            <div className="h-4 max-w-xl rounded-md bg-slate-100" />
          </div>
        </div>
      </div>
      <div className="panel overflow-hidden">
        <div className="border-b border-slate-200 px-5 py-4 sm:px-6">
          <div className="h-5 w-24 rounded-md bg-slate-100" />
        </div>
        <div className="grid sm:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="min-h-28 border-b border-slate-200 px-5 py-5 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0 sm:px-6">
              <div className="h-4 w-20 rounded-md bg-slate-100" />
              <div className="mt-3 h-5 w-32 rounded-md bg-slate-100" />
              <div className="mt-3 h-4 w-full rounded-md bg-slate-100" />
            </div>
          ))}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((item) => (
          <div key={item} className="panel h-72 bg-white" />
        ))}
      </div>
    </div>
  );
}
