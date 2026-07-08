import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const response = NextResponse.json({ success: true });
  response.cookies.set("session_token", "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
  return response;
}

export async function GET(req: NextRequest) {
  // 用相对 Location 跳转，浏览器按当前地址栏解析回到 /login。
  // 不要用 new URL("/login", req.url)：容器内 req.url 的 host 是 0.0.0.0:3000，
  // 会拼出浏览器打不开的绝对地址。
  const response = new NextResponse(null, {
    status: 302,
    headers: { Location: "/login" },
  });
  response.cookies.set("session_token", "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
  return response;
}
