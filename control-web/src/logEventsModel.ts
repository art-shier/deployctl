export interface LogRule {
  id: string;
  name: string;
  kind: "specified" | "percentage";
  enabled: boolean;
  attribute?: "device" | "user" | "model";
  values?: string[];
  percentage: number;
}
export interface LogPolicy {
  version: number;
  rules: LogRule[];
}
export interface RuleDraft {
  id: string;
  name: string;
  kind: LogRule["kind"];
  enabled: boolean;
  attribute: NonNullable<LogRule["attribute"]>;
  valuesText: string;
  percentage: number;
}
export interface Decision {
  allowed: boolean;
  matchedRules: { ruleId: string; matchedBy: string }[];
  policyVersion: number;
}
export interface AppLog {
  event_id: string;
  occurred_at: string;
  received_at: string;
  level: string;
  source: string;
  event: string;
  message: string;
  user_id?: string;
  device_id?: string;
  request_id?: string;
  [key: string]: unknown;
}
export interface LogPage {
  items: AppLog[];
  next_cursor?: number;
}
export function parseRule(d: RuleDraft): LogRule {
  if (!d.name.trim() || d.name.length > 160)
    throw Error("请填写不超过 160 字符的规则名称。");
  const r: LogRule = {
    id: d.id,
    name: d.name.trim(),
    kind: d.kind,
    enabled: d.enabled,
    percentage: 0,
  };
  if (d.kind === "percentage") {
    if (
      !Number.isFinite(d.percentage) ||
      d.percentage < 0 ||
      d.percentage > 100
    )
      throw Error("灰度比例须为 0–100。");
    r.percentage = d.percentage;
  } else {
    r.attribute = d.attribute;
    r.values = [
      ...new Set(
        d.valuesText
          .split(/[\n,，]/)
          .map((v) => v.trim())
          .filter(Boolean),
      ),
    ];
    if (
      !r.values.length ||
      r.values.length > 500 ||
      r.values.some((v) => v.length > 256)
    )
      throw Error("请填写 1–500 个匹配值，每个不超过 256 字符。");
  }
  return r;
}
export function logQuery(filters: Record<string, string>, before?: number) {
  const p = new URLSearchParams({ limit: "100" });
  Object.entries(filters).forEach(([k, v]) => {
    if (v) p.set(k, v);
  });
  if (before) p.set("before", String(before));
  return p.toString();
}
