import { NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://backend:8000/api";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  const body = await request.text();
  const response = await fetch(`${SERVER_API_BASE_URL}/questionnaires/${token}/submit`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
  });

  const text = await response.text();
  return new NextResponse(text, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}
