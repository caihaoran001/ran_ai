export const API_BASE =
  typeof window === "undefined"
    ? (process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api")
    : "/api/backend";

export const FRONTEND_BASE =
  process.env.FRONTEND_PUBLIC_BASE_URL ??
  process.env.NEXT_PUBLIC_FRONTEND_PUBLIC_BASE_URL ??
  "http://localhost:3002";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(typeof window === "undefined"
        ? {"x-admin-api-key": process.env.BACKEND_ADMIN_API_KEY ?? "change-me-internal-api-key"}
        : {}),
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
  ai_auto_reply_enabled: boolean;
  deleted_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  risk_level?: string;
  risk_flags?: Array<Record<string, string>> | null;
  needs_human_intervention?: boolean;
  intervention_reasons?: Array<Record<string, string>> | null;
  renewal_intent_flags?: Array<Record<string, string | boolean>> | null;
  refund_intent_flags?: Array<Record<string, string | boolean>> | null;
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
  content_json?: DocumentContentJson | null;
  file_path?: string | null;
  status: string;
  reviewed_at?: string | null;
  sent_at?: string | null;
};

export type DocumentTableCell = {
  value: string;
  editable?: boolean;
};

export type DocumentContentBlock = {
  key: string;
  title: string;
  kind: "text" | "list" | "table";
  display?: "inline";
  content?: string;
  items?: string[];
  rows?: DocumentTableCell[][];
};

export type DocumentContentJson = {
  template_version: string;
  title: string;
  recipient?: string;
  stage?: string;
  blocks: DocumentContentBlock[];
};

export type Checkin = {
  id: number;
  service_case_id?: number;
  group_id?: string;
  user_id?: number | null;
  checkin_date: string;
  content: string;
  parsed_json?: CheckinParsed | null;
  created_at: string;
};

export type CheckinParsed = {
  product_schedule?: {
    morning_c?: string;
    noon_b?: string;
    night_a?: string;
    summary?: string;
  };
  water?: string;
  sleep?: string;
  mood?: string;
  exercise?: string;
  weight?: string;
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
  analysis_json?: Record<string, unknown> | null;
  trigger?: Message | null;
  created_at: string;
};

export type KnowledgeDraft = {
  id: number;
  draft_type: "qa" | "case" | string;
  status: "pending" | "approved" | "rejected" | string;
  title: string;
  question: string;
  answer: string;
  category?: string | null;
  tags?: string[] | null;
  reason?: string | null;
  evidence?: string | null;
  sanitized_text?: string | null;
  source_message_ids?: number[] | null;
  content_json?: Record<string, unknown> | null;
  content_hash: string;
  published_to?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  rejected_reason?: string | null;
  created_at: string;
  updated_at: string;
};

export type KnowledgeIndexStats = {
  total_chunks: number;
  embedded_chunks: number;
  by_source_type: Record<string, number>;
};

export type KnowledgeFAQ = {
  id: number;
  faq_uid: string;
  status: "active" | "disabled" | "archived" | string;
  title: string;
  question: string;
  answer: string;
  category?: string | null;
  tags?: string[] | null;
  source_type?: string | null;
  source_id?: string | null;
  version: number;
  disabled_at?: string | null;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type KnowledgeEvolutionSetting = {
  enabled: boolean;
  interval_seconds: number;
  qa_at: string;
  daily_qa_limit: number;
  case_days: string;
  case_limit_per_case: number;
  timezone: string;
};
