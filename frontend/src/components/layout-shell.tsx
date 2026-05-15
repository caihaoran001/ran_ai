"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";

const navItems = [
  { href: "/users", label: "用户管理", icon: "👥" },
  { href: "/documents", label: "文档审核", icon: "📄" },
  { href: "/questionnaire-config", label: "问卷配置", icon: "⚙️" },
  { href: "/dashboard", label: "数据看板", icon: "📊" },
];

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isQuestionnaire = pathname?.startsWith("/q/");

  if (isQuestionnaire) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-zinc-50 to-zinc-100">
        <main className="py-8">
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <aside className="fixed inset-y-0 left-0 z-50 w-64 border-r border-zinc-200 bg-white shadow-sm">
        <div className="flex h-16 items-center border-b border-zinc-100 px-6">
          <div className="text-xl font-bold text-zinc-900">燃AI <span className="text-blue-600">1.0</span></div>
        </div>
        <nav className="space-y-1 p-4">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-blue-50 text-blue-700"
                    : "text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900"
                }`}
              >
                <span className="text-lg">{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>
        
        <div className="absolute bottom-0 left-0 right-0 border-t border-zinc-100 p-4">
          <div className="text-xs text-zinc-400">
            燃AI 内部运营系统
          </div>
        </div>
      </aside>

      <main className="pl-64">
        <div className="mx-auto max-w-7xl px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  );
}
