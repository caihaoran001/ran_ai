import { NextRequest, NextResponse } from "next/server";
import { createSessionToken } from "@/lib/auth";

export async function POST(req: NextRequest) {
  try {
    const { username, password } = await req.json();
    
    const expectedUser = process.env.ADMIN_USERNAME || "admin";
    const expectedPassword = process.env.ADMIN_PASSWORD || "change-me";
    
    if (username === expectedUser && password === expectedPassword) {
      const token = await createSessionToken(username);
      const response = NextResponse.json({ success: true });
      
      response.cookies.set("session_token", token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        path: "/",
        maxAge: 24 * 60 * 60, // 24 hours
      });
      
      return response;
    }
    
    return NextResponse.json(
      { error: "用户名或密码错误" },
      { status: 401 }
    );
  } catch (err) {
    return NextResponse.json(
      { error: "请求格式不正确" },
      { status: 400 }
    );
  }
}
