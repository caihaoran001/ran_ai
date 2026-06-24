import { NextRequest, NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 短链入口：用户访问 /s/{code} → 解析出站内目标路径 → 重定向。
// 解析失败(短码不存在/过期)回首页，避免裸 404。
export async function GET(request: NextRequest, { params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  try {
    const res = await fetch(`${SERVER_API_BASE_URL}/short-links/${encodeURIComponent(code)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    const data = await res.json();
    const target = typeof data.target_path === "string" && data.target_path ? data.target_path : "/";
    return NextResponse.redirect(new URL(target, request.url));
  } catch {
    return NextResponse.redirect(new URL("/", request.url));
  }
}
