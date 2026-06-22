import { NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const response = await fetch(`${SERVER_API_BASE_URL}/events/stream`, {
    // 浏览器关闭标签/刷新时，及时把中止信号透传给后端，让后端 SSE 协程尽快结束、
    // 释放数据库连接，避免僵尸长连接在后端堆积(配合后端 asyncio.shield 一起根治连接泄漏)
    signal: request.signal,
    headers: {
      accept: "text/event-stream",
      "x-admin-api-key": process.env.BACKEND_ADMIN_API_KEY ?? "change-me-internal-api-key",
    },
    cache: "no-store",
  });

  const headers = new Headers();
  headers.set("content-type", response.headers.get("content-type") ?? "text/event-stream");
  headers.set("cache-control", "no-cache, no-transform");
  headers.set("connection", "keep-alive");

  return new NextResponse(response.body, {
    status: response.status,
    headers,
  });
}
