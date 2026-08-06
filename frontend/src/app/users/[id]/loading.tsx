export default function UserDetailLoading() {
  return (
    <div className="page-stack" aria-busy="true" aria-label="正在打开用户记录">
      <div className="page-header">
        <div className="page-header-row">
          <div className="flex items-start gap-3">
            {/* 与真实页面同尺寸的返回位，加载完标题不跳动 */}
            <div className="mt-0.5 h-9 w-9 shrink-0 animate-pulse rounded-xl bg-slate-100 motion-reduce:animate-none" />
            <div className="min-w-0 space-y-2">
              <div className="h-7 w-40 animate-pulse rounded bg-slate-200 motion-reduce:animate-none" />
              <div className="h-4 w-56 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
            </div>
          </div>
          <div className="h-6 w-24 animate-pulse rounded-full bg-slate-100 motion-reduce:animate-none" />
        </div>
        <p className="mt-3 text-xs text-slate-500" role="status" aria-live="polite">
          正在打开用户记录，请稍候…
        </p>
      </div>

      <div className="animate-pulse space-y-6 motion-reduce:animate-none">
        {/* 运营概览 */}
        <div className="panel grid gap-4 p-5 sm:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="space-y-2">
              <div className="h-3 w-16 rounded bg-slate-100" />
              <div className="h-7 w-20 rounded bg-slate-200" />
            </div>
          ))}
        </div>

        {/* 画像 / 健康档案 */}
        {[0, 1].map((item) => (
          <div key={item} className="panel space-y-3 p-5">
            <div className="h-5 w-28 rounded bg-slate-200" />
            <div className="h-3 w-full rounded bg-slate-100" />
            <div className="h-3 w-10/12 rounded bg-slate-100" />
            <div className="h-3 w-8/12 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    </div>
  );
}
