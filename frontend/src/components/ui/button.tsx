import * as React from "react";
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
}

export function Button({ className, variant = "primary", ...rest }: ButtonProps) {
  return <button className={cn(variantClass[variant], className)} {...rest} />;
}
