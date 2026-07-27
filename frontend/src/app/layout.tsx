import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { LayoutShell } from "@/components/layout-shell";

export const metadata: Metadata = {
  title: "燃人AI 2.0",
  description: "燃人AI内部运营系统",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <LayoutShell>{children}</LayoutShell>
      </body>
    </html>
  );
}
