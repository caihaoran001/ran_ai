"use client";

import { usePathname } from "next/navigation";

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isQuestionnaire = pathname.startsWith("/q/");

  if (isQuestionnaire) {
    return (
      <div className="min-h-screen bg-zinc-50">
        <main className="py-8">
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-56 border-r border-zinc-200 bg-white px-4 py-5 md:block">
        <div className="text-lg font-semibold">燃AI 1.0</div>
        <nav className="mt-6 grid gap-1 text-sm">
          <a className="rounded-md px-3 py-2 hover:bg-zinc-100" href="/users">用户</a>
          <a className="rounded-md px-3 py-2 hover:bg-zinc-100" href="/cases">服务单</a>
          <a className="rounded-md px-3 py-2 hover:bg-zinc-100" href="/questionnaire-config">问卷配置</a>
          <a className="rounded-md px-3 py-2 hover:bg-zinc-100" href="/checkins">打卡记录</a>
          <a className="rounded-md px-3 py-2 hover:bg-zinc-100" href="/dashboard">基础看板</a>
        </nav>
      </aside>
      <main className="md:pl-56">
        <div className="mx-auto max-w-6xl px-4 py-6">{children}</div>
      </main>
    </div>
  );
}
