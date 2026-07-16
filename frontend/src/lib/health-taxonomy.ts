export type HealthCondition = {
  code: string;
  status: "confirmed" | "pending" | "resolved";
  evidence: string;
  source: string;
};

export type HealthMedication = {
  category_code: string;
  name: string;
  long_term: boolean;
  status: "active" | "stopped" | "unknown";
  evidence: string;
  source: string;
};

export type HealthSpecialStage = {
  code: string;
  status: "active" | "resolved";
  evidence: string;
  source: string;
};

export type HealthAllergy = {
  category_code: string;
  substance: string;
  status: "confirmed" | "pending";
  evidence: string;
  source: string;
};

export type HealthProfile = {
  conditions?: HealthCondition[];
  medications?: HealthMedication[];
  special_stages?: HealthSpecialStage[];
  allergies?: HealthAllergy[];
};

export const HEALTH_DOMAINS: Record<string, string> = {
  cardiovascular: "心血管",
  glucose_metabolism: "血糖代谢",
  lipid_metabolism: "血脂代谢",
  thyroid: "甲状腺",
  reproductive_endocrine: "生殖内分泌",
  hepatobiliary: "肝胆",
  renal_urinary: "肾脏泌尿",
  mental_health: "精神心理",
};

export const HEALTH_CONDITIONS: Record<string, { domain: string; label: string }> = {
  hypertension: { domain: "cardiovascular", label: "高血压" },
  elevated_blood_pressure: { domain: "cardiovascular", label: "血压偏高" },
  type2_diabetes: { domain: "glucose_metabolism", label: "2型糖尿病" },
  diabetes_unspecified: { domain: "glucose_metabolism", label: "糖尿病（类型未明确）" },
  dyslipidemia: { domain: "lipid_metabolism", label: "高血脂/血脂异常" },
  hypothyroidism: { domain: "thyroid", label: "甲状腺功能减退" },
  hyperthyroidism: { domain: "thyroid", label: "甲状腺功能亢进" },
  thyroid_nodule: { domain: "thyroid", label: "甲状腺结节" },
  post_thyroidectomy: { domain: "thyroid", label: "甲状腺术后" },
  pcos: { domain: "reproductive_endocrine", label: "多囊卵巢综合征" },
  hyperprolactinemia: { domain: "reproductive_endocrine", label: "高泌乳素血症" },
  fatty_liver: { domain: "hepatobiliary", label: "脂肪肝" },
  cholecystitis: { domain: "hepatobiliary", label: "胆囊炎" },
  gallstone: { domain: "hepatobiliary", label: "胆结石" },
  liver_condition_unspecified: { domain: "hepatobiliary", label: "肝脏情况未明确" },
  renal_impairment: { domain: "renal_urinary", label: "肾功能异常" },
  kidney_stone: { domain: "renal_urinary", label: "肾结石" },
  depression_diagnosis: { domain: "mental_health", label: "明确抑郁诊断" },
};

export const MEDICATION_CATEGORIES: Record<string, string> = {
  thyroid_hormone: "甲状腺激素用药",
  antihypertensive: "降压药",
  glucose_lowering: "降糖/胰岛素抵抗用药",
  lipid_lowering: "降脂药",
  prolactin_lowering: "降泌乳素用药",
  endocrine_therapy: "内分泌治疗用药",
  other_prescription: "其他处方药",
};

export const SPECIAL_STAGES: Record<string, string> = {
  pregnancy: "已确认孕期",
  lactation: "哺乳期",
  postoperative_recovery: "近期术后恢复",
};

export const ALLERGY_CATEGORIES: Record<string, string> = {
  drug_allergy: "药物过敏",
  food_allergy: "食物过敏",
  product_ingredient_allergy: "产品成分过敏",
  other_allergy: "其他过敏",
};

export function hasHealthProfile(profile?: HealthProfile | null): boolean {
  return Boolean(
    profile &&
      [profile.conditions, profile.medications, profile.special_stages, profile.allergies].some(
        (items) => items && items.length > 0,
      ),
  );
}

export function healthProfileHeadline(profile?: HealthProfile | null): string | null {
  const condition = profile?.conditions?.find((item) => item.status === "confirmed");
  if (condition) {
    const definition = HEALTH_CONDITIONS[condition.code];
    return definition ? `${HEALTH_DOMAINS[definition.domain]} · ${definition.label}` : null;
  }
  const stage = profile?.special_stages?.find((item) => item.status === "active");
  if (stage) return SPECIAL_STAGES[stage.code] || null;
  const medication = profile?.medications?.find((item) => item.long_term && item.status !== "stopped");
  if (medication) return `长期用药 · ${MEDICATION_CATEGORIES[medication.category_code] || medication.name}`;
  return null;
}
