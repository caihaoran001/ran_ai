import { NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const response = await fetch(`${SERVER_API_BASE_URL}/events/stream`, {
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
