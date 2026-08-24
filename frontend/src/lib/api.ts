export const API_BASE =
  typeof window === "undefined"
    ? (process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api")
    : "/api/backend";

// C 端公开入口(问卷展示等)服务端直连常驻 ingress:8001，避开 backend 重启导致的 502。
// 未配 SERVER_INGRESS_BASE_URL(如本地 dev 无 ingress)时回退 backend，行为不变。
// 浏览器端仍走同源代理，不直连内部地址。
export const INGRESS_BASE =
  typeof window === "undefined"
    ? (process.env.SERVER_INGRESS_BASE_URL ?? process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api")
    : "/api/backend";

export const FRONTEND_BASE =
  process.env.FRONTEND_PUBLIC_BASE_URL ??
  process.env.NEXT_PUBLIC_FRONTEND_PUBLIC_BASE_URL ??
  "http://localhost:3002";

export async function api<T>(path: string, init?: RequestInit, baseUrl: string = API_BASE): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
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
  group_name?: string | null;
  /** new_group=本系统入营问卷已提交(新群)；legacy=老群/未填问卷 */
  group_type: "new_group" | "legacy" | string;
  questionnaire_token: string;
  status: string;
  notes?: string | null;
  auto_send_documents: boolean;
  ai_auto_reply_enabled: boolean;
  ai_introduced_at?: string | null;
  ai_introduced_by_wecom_user_id?: string | null;
  ai_introduction_trigger_message_id?: number | null;
  deleted_at?: string | null;
  started_at?: string | null;
  service_plan_start_date?: string | null;
  ended_at?: string | null;
  /** 客户本人已退群的时间；非空即显示「已退群」，群位已让给下一位客户 */
  member_left_at?: string | null;
  checkin_count?: number | null;
  service_continued_at?: string | null;
  content_generated_at?: string | null;
  questionnaire_to_llm_output_ms?: number | null;
  risk_level?: string;
  risk_flags?: Array<Record<string, string>> | null;
  health_profile?: import("@/lib/health-taxonomy").HealthProfile | null;
  intervention_reasons?: Array<Record<string, string>> | null;
  renewal_intent_flags?: Array<Record<string, string | boolean>> | null;
  refund_intent_flags?: Array<Record<string, string | boolean>> | null;
  refund_pending_confirmation?: boolean;
  /** normal / plateau / insufficient_data（平台期检测结果） */
  plateau_status?: string;
  plateau_evidence?: Record<string, unknown> | null;
  plateau_updated_at?: string | null;
  /** 运营在审核控制台标记「已跟进」的时间；结论之后再变一次即重新待跟进 */
  plateau_followed_up_at?: string | null;
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
  checkin_days: number;
  complete_checkin_days: number;
  last_checkin_at?: string | null;
  ai_total_calls: number;
  ai_total_tokens: number;
  ai_estimated_cost_yuan: number;
  ai_deepseek_cost_yuan: number;
  ai_vision_cost_yuan: number;
};

export type RewardQualification = {
  id: number;
  tier_days: number;
  reward: string;
  qualified_on: string;
  shipment_id?: number | null;
  shipment_status?: string | null;
};

export type RewardShipment = {
  id: number;
  status: string;
  rewards: string;
  recipient_name?: string | null;
  recipient_phone?: string | null;
  shipping_address?: string | null;
  requested_at: string;
  shipped_at?: string | null;
  marked_by?: string | null;
};

export type RewardCheckinItem = {
  order: number;
  item_id: string;
  question: string;
  answer: string;
};

export type RewardCheckinSourceMessage = {
  id: number;
  message_type: string;
  content: string;
  created_at: string;
};

export type RewardCheckinDay = {
  checkin_id: number;
  checkin_date: string;
  cumulative_day: number;
  items: RewardCheckinItem[];
  source_messages: RewardCheckinSourceMessage[];
  original_submission?: string | null;
};

export type RewardAdminCase = {
  service_case_id: number;
  case_no: string;
  user_name: string;
  group_id: string;
  group_name?: string | null;
  service_status: string;
  complete_checkin_days: number;
  current_choice?: string | null;
  unshipped_rewards: string;
  operational_status: string;
  qualifications: RewardQualification[];
  shipments: RewardShipment[];
  checkin_days: RewardCheckinDay[];
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

export type UserStateProfile = {
  id: number;
  service_case_id: number;
  state_json: Record<string, unknown>;
  summary?: string | null;
  source: string;
  created_at: string;
  updated_at: string;
};

export type UserOperationsFact = {
  domain: string;
  domain_label: string;
  field_key?: string | null;
  value: string;
  evidence?: string | null;
  source?: string | null;
  occurred_at?: string | null;
};

export type UserOperationsSnapshot = {
  attention_code: string;
  attention_label: string;
  attention_reason: string;
  confirmed_guidance?: UserOperationsFact | null;
  latest_change?: UserOperationsFact | null;
  checkin_days: number;
  complete_checkin_days: number;
  last_checkin_at?: string | null;
  updated_at?: string | null;
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
  latest_state_profile?: UserStateProfile | null;
  operations_snapshot?: UserOperationsSnapshot | null;
  created_at: string;
  updated_at: string;
};

export type DocumentStatus =
  | "draft"
  | "reviewing"
  | "approved"
  | "sent"
  | "archived"
  | "superseded";

export type UserDocument = {
  id: number;
  service_case_id: number;
  document_type: string;
  title: string;
  content_md: string;
  content_json?: DocumentContentJson | null;
  file_path?: string | null;
  status: DocumentStatus | string;
  reviewed_at?: string | null;
  sent_at?: string | null;
};

export type DashboardCaseRef = {
  service_case_id: number;
  case_no: string;
  user_id?: number | null;
  user_name?: string | null;
  user_phone?: string | null;
  group_id: string;
  group_name?: string | null;
};

export type DashboardPendingDocument = {
  document_id: number;
  document_type: string;
  title: string;
  status: DocumentStatus | string;
  created_at: string;
  updated_at: string;
  /** 内部运营状态：需人工复核的入营文件不会自动发送，必须运营检查后手动确认发送。 */
  manual_review_required?: boolean;
  manual_review_reasons?: string[];
  /** 模型故障时系统兜底生成的占位稿，之后会自动重做替换。 */
  degraded?: boolean;
};

export type DashboardCasePendingDocuments = {
  case: DashboardCaseRef;
  documents: DashboardPendingDocument[];
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

/** 内部复核元数据：只存在于 content_json 顶层，不参与 DOCX / Markdown 正文渲染。 */
export type DocumentInternalReview = {
  manual_review_required?: boolean;
  reasons?: string[];
  safety_status?: string;
  provisional_band?: boolean;
  band_confidence?: string;
};

export type DocumentContentJson = {
  template_version: string;
  title: string;
  recipient?: string;
  stage?: string;
  blocks: DocumentContentBlock[];
  internal_review?: DocumentInternalReview;
  /** 模型故障时系统兜底生成的占位稿，之后会自动重做替换。 */
  degraded?: boolean;
};

export type Checkin = {
  id: number;
  service_case_id?: number;
  group_id?: string;
  user_id?: number | null;
  checkin_date: string;
  content: string;
  parsed_json?: CheckinParsed | null;
  is_complete: boolean;
  created_at: string;
};

export type CheckinCustomAnswer = {
  answer?: string;
  question?: string;
  source?: string;
  evidence_message_id?: string | number | null;
  questionnaire_submission_id?: string | number | null;
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
  waist?: string;
  diet?: string;
  bowel?: string;
  hunger?: string;
  sensation?: string;
  discomfort?: string;
  tomorrow_focus?: string;
  custom_checkin?: Record<string, CheckinCustomAnswer | string>;
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
  display_at?: string;
  display_sender_name?: string | null;
  created_at: string;
};

export type OutcomeEvent = {
  id: number;
  service_case_id: number;
  message_id?: number | null;
  milestone: string;
  field_key: string;
  event_type: string;
  value_text: string;
  evidence_text: string;
  confidence: number;
  source: string;
  occurred_at?: string | null;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type OutcomeStageState = {
  id: number;
  service_case_id: number;
  milestone: string;
  status: string;
  collected_fields?: Record<string, OutcomeCollectedField> | null;
  missing_fields?: string[] | null;
  created_at: string;
  updated_at: string;
};

export type OutcomeCollectedField = {
  value?: string;
  evidence_text?: string;
  event_type?: string;
  confidence?: number;
  message_id?: number;
  updated_at?: string;
};

export type OutcomeSnapshot = {
  id: number;
  service_case_id: number;
  milestone: string;
  status: string;
  metrics_json?: Record<string, unknown> | null;
  evidence_json?: Record<string, unknown> | null;
  ai_summary?: string | null;
  action_items?: Array<Record<string, unknown>> | null;
  generated_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type OutcomeBundle = {
  events: OutcomeEvent[];
  states: OutcomeStageState[];
  snapshots: OutcomeSnapshot[];
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

export type KnowledgeBusinessConflict = {
  code: "knowledge_business_conflict";
  message: string;
  reason: string;
  draft_type: "qa" | "case" | string;
  existing: {
    chunk_id: number;
    title: string;
    question: string;
    answer: string;
    category?: string | null;
    tags?: string[] | null;
  };
  candidate: {
    title: string;
    question: string;
    answer: string;
    category?: string | null;
    tags?: string[] | null;
  };
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
  confirmation_state?: "confirmed" | "pending" | string;
  last_confirmed_version?: number | null;
  last_confirmed_at?: string | null;
};

export type DiagnosticsSummary = {
  database: {
    status: "connected" | "failed" | "checking" | string;
    message: string;
  };
  ai_probe: {
    status: "connected" | "failed" | "not_configured" | "checking" | string;
    message: string;
    latency_ms?: number | null;
    model?: string | null;
  };
  diagnostic_costs?: {
    latest_run_id?: string | null;
    latest: { calls: number; tokens: number; estimated_cost_yuan: number };
    lifetime: { calls: number; tokens: number; estimated_cost_yuan: number };
  };
  checked_at: number;
};

export type PopulationDistributionBucket = {
  label: string;
  count: number;
};

export type PopulationDistribution = {
  key: string;
  label: string;
  description: string;
  multi_select: boolean;
  total: number;
  unknown: number;
  buckets: PopulationDistributionBucket[];
};

export type PopulationGeoCity = {
  city: string;
  count: number;
};

export type PopulationGeoProvince = {
  province: string;
  count: number;
  cities: PopulationGeoCity[];
};

export type PopulationGeography = {
  total: number;
  unknown: number;
  provinces: PopulationGeoProvince[];
};

export type PopulationPortrait = {
  total_users: number;
  distributions: PopulationDistribution[];
  geography: PopulationGeography | null;
};

export type PopulationInsightJTBD = {
  when: string;
  want_to: string;
  so_i_can: string;
};

export type PopulationInsightSegment = {
  name: string;
  entry_point: string;
  deeper_goal: string;
  evidence_basis: string;
};

export type PopulationInsightContent = {
  headline: string;
  summary: string;
  common_jtbd: PopulationInsightJTBD;
  segments: PopulationInsightSegment[];
  shared_patterns: string[];
  product_implications: string[];
  evidence_limits: string[];
};

export type PopulationInsight = {
  status: "empty" | "generating" | "ready" | "failed";
  content: PopulationInsightContent | null;
  included_users: number;
  profiled_users: number;
  model_input_users: number;
  evidence_level: string;
  generated_at: string | null;
  generated_by_model: string | null;
  refresh_reason: string | null;
  next_automatic_refresh_at: string | null;
  last_refresh_failed: boolean;
};
