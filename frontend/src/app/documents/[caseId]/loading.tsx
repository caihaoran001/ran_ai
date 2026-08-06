export default function DocumentDetailLoading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="正在打开交付文件">
      <div>
        <h1 className="text-2xl font-semibold">交付文件审核</h1>
        <p className="mt-1 text-sm text-slate-500" role="status" aria-live="polite">
          正在打开文件，请稍候…
        </p>
      </div>

      <div className="panel animate-pulse space-y-5 p-5 motion-reduce:animate-none">
        {/* 标题与状态行 */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
          <div className="space-y-2">
            <div className="h-5 w-56 rounded bg-slate-200" />
            <div className="h-3 w-40 rounded bg-slate-100" />
          </div>
          <div className="flex gap-2">
            <div className="h-9 w-24 rounded-lg bg-slate-100" />
            <div className="h-9 w-28 rounded-lg bg-slate-200" />
          </div>
        </div>

        {/* 正文骨架 */}
        <div className="space-y-3">
          <div className="h-4 w-1/3 rounded bg-slate-200" />
          <div className="h-3 w-full rounded bg-slate-100" />
          <div className="h-3 w-11/12 rounded bg-slate-100" />
          <div className="h-3 w-4/5 rounded bg-slate-100" />
          <div className="mt-6 h-4 w-1/4 rounded bg-slate-200" />
          <div className="h-3 w-full rounded bg-slate-100" />
          <div className="h-3 w-10/12 rounded bg-slate-100" />
          <div className="h-3 w-9/12 rounded bg-slate-100" />
        </div>
      </div>
    </div>
  );
}
