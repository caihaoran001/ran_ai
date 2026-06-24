import { NextRequest, NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 短链入口：用户访问 /s/{code} → 解析出站内目标路径 → 重定向。
// 解析失败(短码不存在/过期)回首页，避免裸 404。
// 返回相对路径 Location，让浏览器基于用户当前访问的域名解析；
// 不要用 new URL(target, request.url)——容器内 request.url 的 host 是内部地址(0.0.0.0:3000)，会跳错。
function redirectTo(path: string): NextResponse {
  // 只允许站内绝对路径，防开放重定向(//evil.com 这类协议相对外链)
  const safe = path.startsWith("/") && !path.startsWith("//") ? path : "/";
  return new NextResponse(null, { status: 307, headers: { Location: safe } });
}

export async function GET(_request: NextRequest, { params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  try {
    const res = await fetch(`${SERVER_API_BASE_URL}/short-links/${encodeURIComponent(code)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return redirectTo("/");
    }
    const data = await res.json();
    const target = typeof data.target_path === "string" ? data.target_path : "/";
    return redirectTo(target);
  } catch {
    return redirectTo("/");
  }
}
