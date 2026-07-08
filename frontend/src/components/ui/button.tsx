import * as React from "react";
import { cn } from "@/lib/utils";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className, ...rest } = props;
  return (
    <button
      className={cn(
        "inline-flex min-h-11 cursor-pointer items-center justify-center rounded-lg bg-cyan-800 px-4 text-sm font-bold text-white transition-colors duration-200 hover:bg-cyan-900 active:bg-cyan-950 disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  );
}
