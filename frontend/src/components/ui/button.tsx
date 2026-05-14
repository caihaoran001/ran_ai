import * as React from "react";
import { cn } from "@/lib/utils";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className, ...rest } = props;
  return (
    <button
      className={cn(
        "inline-flex h-9 items-center justify-center rounded-md bg-zinc-900 px-3 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-700 disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  );
}
