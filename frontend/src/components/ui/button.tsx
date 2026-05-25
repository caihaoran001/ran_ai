import * as React from "react";
import { cn } from "@/lib/utils";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className, ...rest } = props;
  return (
    <button
      className={cn(
        "inline-flex h-9 items-center justify-center rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition-all duration-200 hover:bg-blue-500 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none disabled:active:scale-100",
        className,
      )}
      {...rest}
    />
  );
}
