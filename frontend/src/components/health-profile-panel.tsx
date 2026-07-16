import { AlertTriangle, HeartPulse, Pill, ShieldAlert } from "lucide-react";
import {
  ALLERGY_CATEGORIES,
  HEALTH_CONDITIONS,
  HEALTH_DOMAINS,
  hasHealthProfile,
  type HealthProfile,
  MEDICATION_CATEGORIES,
  SPECIAL_STAGES,
} from "@/lib/health-taxonomy";

export function HealthProfilePanel({ profile }: { profile?: HealthProfile | null }) {
  if (!hasHealthProfile(profile)) return null;

  const confirmed = profile?.conditions?.filter((item) => item.status === "confirmed") || [];
  const pending = profile?.conditions?.filter((item) => item.status === "pending") || [];
  const medications = profile?.medications?.filter((item) => item.status !== "stopped") || [];
  const stages = profile?.special_stages?.filter((item) => item.status === "active") || [];
  const allergies = profile?.allergies?.filter((item) => item.status === "confirmed") || [];

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="panel-title">结构化健康标签</h2>
            <p className="panel-subtitle">疾病、用药和特殊阶段分层记录；原话证据用于人工核验。</p>
          </div>
          <span className="status-pill pill-neutral">统一画像来源</span>
        </div>
      </div>
      <div className="divide-y divide-slate-200">
        {confirmed.length > 0 && (
          <HealthFactGroup
            icon={HeartPulse}
            title="已明确健康情况"
            items={confirmed.map((item) => {
              const definition = HEALTH_CONDITIONS[item.code];
              return {
                tags: definition
                  ? [HEALTH_DOMAINS[definition.domain], definition.label]
                  : [item.code],
                evidence: item.evidence,
              };
            })}
          />
        )}
        {medications.length > 0 && (
          <HealthFactGroup
            icon={Pill}
            title="用药情况"
            items={medications.map((item) => ({
              tags: [
                MEDICATION_CATEGORIES[item.category_code] || item.category_code,
                item.name,
                ...(item.long_term ? ["长期处方用药"] : []),
              ],
              evidence: item.evidence,
            }))}
          />
        )}
        {stages.length > 0 && (
          <HealthFactGroup
            icon={ShieldAlert}
            title="特殊阶段"
            items={stages.map((item) => ({
              tags: [SPECIAL_STAGES[item.code] || item.code],
              evidence: item.evidence,
            }))}
          />
        )}
        {pending.length > 0 && (
          <HealthFactGroup
            icon={AlertTriangle}
            title="待确认事项"
            tone="pending"
            items={pending.map((item) => ({
              tags: [HEALTH_CONDITIONS[item.code]?.label || item.code, "待确认"],
              evidence: item.evidence,
            }))}
          />
        )}
        {allergies.length > 0 && (
          <HealthFactGroup
            icon={ShieldAlert}
            title="明确过敏史"
            items={allergies.map((item) => ({
              tags: [ALLERGY_CATEGORIES[item.category_code] || item.category_code, item.substance],
              evidence: item.evidence,
            }))}
          />
        )}
      </div>
    </section>
  );
}

function HealthFactGroup({
  icon: Icon,
  title,
  items,
  tone = "default",
}: {
  icon: typeof HeartPulse;
  title: string;
  items: Array<{ tags: string[]; evidence: string }>;
  tone?: "default" | "pending";
}) {
  return (
    <div className="grid gap-3 px-5 py-4 sm:grid-cols-[10rem_1fr] sm:px-6">
      <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
        <Icon className={`h-4 w-4 ${tone === "pending" ? "text-amber-600" : "text-cyan-700"}`} />
        {title}
      </div>
      <div className="space-y-3">
        {items.map((item, index) => (
          <div key={`${item.tags.join("-")}-${index}`} className="min-w-0">
            <div className="flex flex-wrap gap-1.5">
              {item.tags.map((tag) => (
                <span
                  key={tag}
                  className={`status-pill ${tone === "pending" ? "pill-warn" : "pill-info"}`}
                >
                  {tag}
                </span>
              ))}
            </div>
            <p className="mt-1.5 break-words text-sm leading-6 text-slate-600">依据：{item.evidence}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
