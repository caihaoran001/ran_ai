export const API_BASE =
  typeof window === "undefined"
    ? (process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api")
    : (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api");

export const FRONTEND_BASE =
  process.env.FRONTEND_PUBLIC_BASE_URL ??
  process.env.NEXT_PUBLIC_FRONTEND_PUBLIC_BASE_URL ??
  "http://localhost:3002";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export type ServiceCase = {
  id: number;
  case_no: string;
  group_id: string;
  questionnaire_token: string;
  status: string;
  notes?: string | null;
  auto_send_documents: boolean;
  deleted_at?: string | null;
  user?: { id: number; name: string; phone?: string | null; wecom_user_id?: string | null } | null;
  created_at: string;
  updated_at: string;
};

export type ServiceCaseStats = {
  message_count: number;
  inbound_message_count: number;
  outbound_message_count: number;
  avg_daily_inbound_messages: number;
  last_message_at?: string | null;
  last_user_message_at?: string | null;
  checkin_count: number;
  last_checkin_at?: string | null;
};

export type UserProfile = {
  id: number;
  service_case_id: number;
  profile_json: Record<string, unknown>;
  summary: string;
  generated_by_model?: string | null;
  created_at: string;
  updated_at: string;
};

export type User = {
  id: number;
  name: string;
  phone?: string | null;
  wecom_user_id?: string | null;
  notes?: string | null;
  deleted_at?: string | null;
  service_cases: ServiceCase[];
  latest_profile?: UserProfile | null;
  created_at: string;
  updated_at: string;
};

export type UserDocument = {
  id: number;
  service_case_id: number;
  document_type: string;
  title: string;
  content_md: string;
  file_path?: string | null;
  status: string;
};

export type Checkin = {
  id: number;
  checkin_date: string;
  content: string;
  created_at: string;
};

export type Message = {
  id: number;
  service_case_id: number | null;
  group_id: string;
  wecom_message_id: string | null;
  direction: "inbound" | "outbound";
  sender_type: "user" | "bot" | "staff";
  message_type: string;
  content: string;
  purpose: string;
  status: string;
  error: string | null;
  created_at: string;
};
