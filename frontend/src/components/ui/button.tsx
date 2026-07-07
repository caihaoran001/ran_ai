import * as React from "react";
import { cn } from "@/lib/utils";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className, ...rest } = props;
  return (
    <button
      className={cn(
        "inline-flex min-h-11 cursor-pointer items-center justify-center rounded-lg bg-cyan-700 px-4 text-sm font-semibold text-white shadow-sm transition-colors duration-200 hover:bg-cyan-800 disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  );
}
