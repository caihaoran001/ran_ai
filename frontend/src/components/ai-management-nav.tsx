import Link from "next/link";
import { Activity, ShieldCheck } from "lucide-react";

type AIManagementSection = "operations" | "quality";

const sections = [
  {
    key: "operations",
    href: "/agent-operations",
    label: "运行监控",
    icon: Activity,
  },
  {
    key: "quality",
    href: "/rag-evaluations",
    label: "回复质量",
    icon: ShieldCheck,
  },
] as const;

export function AIManagementNav({
  active,
}: {
  active: AIManagementSection;
}) {
  return (
    <nav
      aria-label="AI 管理栏目"
      className="flex min-w-0 gap-1 border-b border-slate-200 px-1"
    >
      {sections.map((section) => {
        const Icon = section.icon;
        const selected = active === section.key;
        return (
          <Link
            key={section.key}
            href={section.href}
            aria-current={selected ? "page" : undefined}
            className={`-mb-px inline-flex min-h-11 min-w-0 items-center justify-center gap-2 border-b-2 px-3 text-sm font-bold transition-colors sm:px-4 ${
              selected
                ? "border-cyan-700 text-cyan-900"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-900"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{section.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
