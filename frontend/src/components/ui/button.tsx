import * as React from "react";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const variantClass: Record<Variant, string> = {
  primary: "btn-primary",
  secondary: "admin-button-secondary",
  danger: "admin-button-danger",
  ghost:
    "btn border border-transparent bg-transparent font-semibold text-slate-600 hover:bg-slate-100 hover:text-slate-900",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
  loadingText?: React.ReactNode;
}

export function Button({
  className,
  variant = "primary",
  loading = false,
  loadingText,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(variantClass[variant], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && (
        <LoaderCircle
          className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none"
          aria-hidden="true"
        />
      )}
      {loading ? (loadingText ?? children) : children}
    </button>
  );
}
