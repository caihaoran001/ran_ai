import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { verifySessionToken } from "./lib/auth";

export async function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  
  // Allow access to public assets, questionnaires, and auth endpoints without auth.
  if (
    pathname === "/login" ||
    pathname === "/api/auth/login" ||
    pathname === "/api/auth/logout" ||
    pathname.startsWith("/q/") ||
    pathname.startsWith("/s/") ||
    // 问卷提交:只有 POST(用户首次填写)公开免登录;PATCH(运营编辑重生成)走下方登录校验
    (req.method === "POST" && pathname.startsWith("/api/questionnaires/") && pathname.endsWith("/submit")) ||
    pathname.startsWith("/_next") ||
    (!pathname.startsWith("/api/") && pathname.includes(".")) // public files like favicon.ico
  ) {
    return NextResponse.next();
  }

  const token = req.cookies.get("session_token")?.value;
  const isAuthenticated = await verifySessionToken(token);

  if (isAuthenticated) {
    return NextResponse.next();
  }

  // Redirect to login for pages, return 401 JSON for APIs
  if (pathname.startsWith("/api/")) {
    return NextResponse.json(
      { detail: "unauthorized" },
      { status: 401 }
    );
  }

  return NextResponse.redirect(new URL("/login", req.url));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
