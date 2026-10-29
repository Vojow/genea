/** Private operational telemetry. Never attach this response to PresentationPayload. */
export const RESEARCH_TELEMETRY_ENDPOINT = "/api/presentation/research-telemetry";

export type MeasurementStatus = "actual" | "estimated" | "unknown" | "partial";

export interface TelemetryMetric {
  value: number | null;
  status: MeasurementStatus;
  unit: string;
  observations: number;
  unknownObservations: number;
}

export interface TelemetryScope {
  kind: "act" | "person" | "generation" | "group" | "batch" | "volume" | "case";
  id: string;
  label: string;
  availability: "available";
  anchorId?: string;
  depth?: number;
}

export interface TelemetryRow {
  id: string;
  label: string;
  scope?: TelemetryScope;
  eventCount: number;
  eventIds: string[];
  batchIds: string[];
  metrics: Record<string, TelemetryMetric>;
  workItems: { status: "unknown"; reason: "unsupported" };
}

export interface ResearchTelemetry {
  schemaVersion: 1;
  generatedAt: string | null;
  status: "ready" | "empty";
  summary: { eventCount: number; eventIds: string[]; metrics: Record<string, TelemetryMetric> };
  batches: TelemetryRow[];
  scopes: TelemetryRow[];
  unsupported: Record<"diskUsage" | "archiveCoverage" | "wallTime" | "workItems", { status: "unknown"; reason: "unsupported" }>;
}

export class TelemetryApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "TelemetryApiError";
  }
}

const UNITS: Record<string, string> = {
  input_tokens: "tokens", cached_input_tokens: "tokens", uncached_input_tokens: "tokens",
  output_tokens: "tokens", reasoning_output_tokens: "tokens", images: "images", calls: "calls",
  retries: "retries", duration_seconds: "seconds", cost_usd: "usd", pages: "pages",
  regions: "regions", hits: "hits", correct_acts: "acts", accepted_fields: "fields",
  resolved_pairs: "pairs", agent_runs: "runs", allowance_points: "points",
};

function invalid(): never {
  throw new TelemetryApiError("Serwer zwrócił nieprawidłowe dane telemetrii.", 502);
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return invalid();
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return invalid();
  return value;
}

function ids(value: unknown): string[] {
  if (!Array.isArray(value)) return invalid();
  const result = value.map(text);
  if (new Set(result).size !== result.length) return invalid();
  return result;
}

function metrics(value: unknown): Record<string, TelemetryMetric> {
  const result: Record<string, TelemetryMetric> = {};
  for (const [name, raw] of Object.entries(record(value))) {
    // Unknown future fields are ignored, never interpreted as a new measurement.
    if (!Object.hasOwn(UNITS, name)) continue;
    const item = record(raw);
    if (typeof item.status !== "string" || !["actual", "estimated", "unknown", "partial"].includes(item.status) || item.unit !== UNITS[name]) return invalid();
    const status = item.status as MeasurementStatus;
    if (status === "unknown" ? item.value !== null : typeof item.value !== "number" || !Number.isFinite(item.value) || item.value < 0) return invalid();
    if (item.value !== null && !["usd", "seconds", "points"].includes(UNITS[name]!) && !Number.isSafeInteger(item.value)) return invalid();
    const observations = count(item.observations);
    const unknownObservations = count(item.unknownObservations);
    if (unknownObservations > observations || (["actual", "estimated"].includes(status) && unknownObservations > 0)) return invalid();
    result[name] = { value: item.value as number | null, status, unit: UNITS[name]!, observations, unknownObservations };
  }
  for (const [part, total] of [["cached_input_tokens", "input_tokens"], ["reasoning_output_tokens", "output_tokens"]]) {
    const subset = result[part!];
    const whole = result[total!];
    if (subset?.value !== null && subset?.value !== undefined && whole?.value !== null && whole?.value !== undefined &&
        subset.status !== "partial" && whole.status !== "partial" && subset.value > whole.value) return invalid();
  }
  return result;
}

function eventSummary(value: unknown): ResearchTelemetry["summary"] {
  const item = record(value);
  const eventIds = ids(item.eventIds);
  const eventCount = count(item.eventCount);
  if (eventIds.length !== eventCount) return invalid();
  return { eventCount, eventIds, metrics: metrics(item.metrics) };
}

function scope(value: unknown): TelemetryScope {
  const item = record(value);
  if (typeof item.kind !== "string" || !["act", "person", "generation", "group", "batch", "volume", "case"].includes(item.kind) || item.availability !== "available") return invalid();
  const result: TelemetryScope = { kind: item.kind as TelemetryScope["kind"], id: text(item.id), label: text(item.label), availability: "available" };
  if (result.kind === "generation") {
    result.anchorId = text(item.anchorId);
    result.depth = count(item.depth);
  }
  return result;
}

function unsupported(value: unknown): { status: "unknown"; reason: "unsupported" } {
  const item = record(value);
  if (item.status !== "unknown" || item.reason !== "unsupported") return invalid();
  return { status: "unknown", reason: "unsupported" };
}

export function parseResearchTelemetry(value: unknown): ResearchTelemetry {
  const item = record(value);
  if (item.schemaVersion !== 1 || typeof item.status !== "string" || !["ready", "empty"].includes(item.status) || !Array.isArray(item.batches) || !Array.isArray(item.scopes)) return invalid();
  if (item.generatedAt !== null && typeof item.generatedAt !== "string") return invalid();
  const batches = item.batches.map((raw): TelemetryRow => {
    const entry = record(raw);
    return { ...eventSummary(entry), id: text(entry.id), label: text(entry.label), batchIds: [text(entry.batchId)], workItems: unsupported(entry.workItems) };
  });
  const scopes = item.scopes.map((raw): TelemetryRow => {
    const entry = record(raw);
    const resolvedScope = scope(entry.scope);
    return { ...eventSummary(entry), id: text(entry.id), label: resolvedScope.label, scope: resolvedScope, batchIds: ids(entry.batchIds), workItems: unsupported(entry.workItems) };
  });
  for (const rows of [batches, scopes]) {
    if (new Set(rows.map((row) => row.id)).size !== rows.length) return invalid();
  }
  const missingSources = record(item.unsupported);
  return {
    schemaVersion: 1, generatedAt: item.generatedAt as string | null, status: item.status as ResearchTelemetry["status"],
    summary: eventSummary(item.summary), batches, scopes,
    unsupported: { diskUsage: unsupported(missingSources.diskUsage), archiveCoverage: unsupported(missingSources.archiveCoverage), wallTime: unsupported(missingSources.wallTime), workItems: unsupported(missingSources.workItems) },
  };
}

export async function loadResearchTelemetry(signal?: AbortSignal): Promise<ResearchTelemetry> {
  const response = await fetch(RESEARCH_TELEMETRY_ENDPOINT, {
    credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal,
  });
  if (!response.ok) {
    const message = response.status === 401 || response.status === 403
      ? "Telemetria wymaga dostępu do prywatnego serwera badań."
      : "Nie udało się pobrać telemetrii badań.";
    throw new TelemetryApiError(message, response.status);
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { return invalid(); }
  return parseResearchTelemetry(payload);
}

export const TELEMETRY_METRICS = [
  ["input_tokens", "Tokeny wejścia", "tokens"],
  ["cached_input_tokens", "Tokeny wejścia z cache", "tokens"],
  ["uncached_input_tokens", "Tokeny wejścia bez cache", "tokens"],
  ["output_tokens", "Tokeny wyjścia (z reasoning)", "tokens"],
  ["reasoning_output_tokens", "Tokeny reasoning (część wyjścia)", "tokens"],
  ["cost_usd", "Koszt", "usd"],
  ["duration_seconds", "Zarejestrowany czas operacji", "seconds"],
  ["pages", "Strony przetworzone", "pages"],
  ["hits", "Trafienia", "hits"],
] as const;

export const measurementStatusLabel = (status: MeasurementStatus): string => ({
  actual: "pomiar (actual)",
  estimated: "estymacja (estimated)",
  unknown: "brak danych (unknown)",
  partial: "znany subtotal (partial)",
})[status];

export function metricValueLabel(metric: TelemetryMetric | undefined): string {
  if (!metric || metric.status === "unknown" || metric.value === null) return "brak danych (unknown)";
  const unit = metric.unit === "usd" ? "USD" : metric.unit === "seconds" ? "s" : "";
  const value = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 6 }).format(metric.value);
  return `${value}${unit ? ` ${unit}` : ""} · ${measurementStatusLabel(metric.status)}`;
}
