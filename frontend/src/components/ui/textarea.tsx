import * as React from "react";
import { cn } from "@/lib/utils";

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className, ...rest } = props;
  return (
    <textarea
      className={cn(
        "min-h-24 w-full min-w-0 rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-medium leading-6 text-slate-900 shadow-[var(--shadow-xs)] outline-none transition-all duration-150 placeholder:font-normal placeholder:text-slate-400 hover:border-slate-400 focus:border-cyan-600 focus:ring-4 focus:ring-cyan-500/15 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500",
        className,
      )}
      {...rest}
    />
  );
}
