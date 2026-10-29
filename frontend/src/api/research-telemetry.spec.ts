import { afterEach, describe, expect, it, vi } from "vitest";

import { loadResearchTelemetry, metricValueLabel, parseResearchTelemetry, RESEARCH_TELEMETRY_ENDPOINT, TelemetryApiError } from "./research-telemetry";

const missingSource = () => ({ value: null, status: "unknown", reason: "unsupported" });
const metric = (value: number | null, status = "actual", unit = "tokens", observations = 1, unknownObservations = 0) => ({ value, status, unit, observations, unknownObservations, aggregation: "operation_sum" });
const fixture = () => ({
  schemaVersion: 1, generatedAt: null, status: "ready",
  summary: { eventCount: 1, eventIds: ["event-1"], metrics: { input_tokens: metric(100), cost_usd: metric(0.2, "partial", "usd", 2, 1) } },
  batches: [{ id: "batch-1", batchId: "batch-1", label: "Partia testowa", eventCount: 1, eventIds: ["event-1"], metrics: { input_tokens: metric(100) }, workItems: missingSource() }],
  scopes: [{ id: "person:person-1", scope: { kind: "person", id: "person-1", label: "Podmiot testowy", availability: "available" }, batchIds: ["batch-1"], eventCount: 1, eventIds: ["event-1"], metrics: { input_tokens: metric(100) }, workItems: missingSource() }],
  unsupported: { diskUsage: missingSource(), archiveCoverage: missingSource(), wallTime: missingSource(), workItems: missingSource() },
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("private research telemetry client", () => {
  it("fetches the private endpoint without caching and forwards cancellation", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => fixture() });
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    const result = await loadResearchTelemetry(controller.signal);
    expect(fetch).toHaveBeenCalledWith(RESEARCH_TELEMETRY_ENDPOINT, {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal,
    });
    expect(result.summary.eventCount).toBe(1);
    expect(result.scopes[0]?.scope?.id).toBe("person-1");
  });

  it("does not expose an error response body containing private details", async () => {
    const json = vi.fn().mockResolvedValue({ secret: "private path" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403, json }));
    await expect(loadResearchTelemetry()).rejects.toThrow("dostępu do prywatnego serwera");
    expect(json).not.toHaveBeenCalled();
  });

  it("rejects HTML or malformed JSON and schema versions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError(); } }));
    await expect(loadResearchTelemetry()).rejects.toBeInstanceOf(TelemetryApiError);
    expect(() => parseResearchTelemetry({ ...fixture(), schemaVersion: 2 })).toThrow(TelemetryApiError);
    expect(() => parseResearchTelemetry({})).toThrow(TelemetryApiError);
  });

  it("preserves unknown and partial without converting missing values to zero", () => {
    const payload = fixture();
    payload.summary.metrics.input_tokens = metric(null, "unknown", "tokens", 1, 1);
    const parsed = parseResearchTelemetry(payload);
    expect(metricValueLabel(parsed.summary.metrics.input_tokens)).toBe("brak danych (unknown)");
    expect(metricValueLabel(parsed.summary.metrics.cost_usd)).toContain("znany subtotal (partial)");
    expect(metricValueLabel(undefined)).toBe("brak danych (unknown)");
    expect(metricValueLabel({ value: 0, status: "actual", unit: "usd", observations: 1, unknownObservations: 0 })).toBe("0 USD · pomiar (actual)");
  });

  it.each([
    metric(-1), metric(0.5), metric(Number.NaN), metric(Number.POSITIVE_INFINITY), metric(0, "unknown"),
    metric(1, "actual", "usd"), metric(1, "invented"), metric(1, "actual", "tokens", 1, 2),
  ])("rejects invalid measurements %#", (invalidMetric) => {
    const payload = fixture();
    payload.summary.metrics.input_tokens = invalidMetric;
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
  });

  it("checks token subsets and does not treat hits as token cache", () => {
    const payload = fixture();
    Object.assign(payload.summary.metrics, { cached_input_tokens: metric(101), hits: metric(50, "actual", "hits") });
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
    Object.assign(payload.summary.metrics, { cached_input_tokens: metric(20), output_tokens: metric(10), reasoning_output_tokens: metric(11) });
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
  });

  it("retains the backend union count rather than summing associated scope cards", () => {
    const payload = fixture();
    payload.scopes.push({ ...payload.scopes[0]!, id: "person:person-2", scope: { kind: "person", id: "person-2", label: "Drugi podmiot", availability: "available" } });
    expect(parseResearchTelemetry(payload).summary.eventCount).toBe(1);
    payload.summary.eventIds.push("event-1");
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
  });

  it("requires explicit generation anchor and depth and resolved privacy availability", () => {
    const payload = fixture();
    Object.assign(payload.scopes[0]!.scope, { kind: "generation" });
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
    Object.assign(payload.scopes[0]!.scope, { anchorId: "person-1", depth: 3 });
    expect(parseResearchTelemetry(payload).scopes[0]?.scope).toMatchObject({ anchorId: "person-1", depth: 3 });
    Object.assign(payload.scopes[0]!.scope, { availability: "restricted" });
    expect(() => parseResearchTelemetry(payload)).toThrow(TelemetryApiError);
  });

  it("drops uncontracted free text and future metric fields", () => {
    const payload = fixture();
    Object.assign(payload.summary.metrics.input_tokens, { notes: ["private log content"] });
    Object.assign(payload.summary.metrics, { hidden_count: metric(3) });
    Object.assign(payload, { rawLedger: "private log content" });
    const parsed = parseResearchTelemetry(payload);
    expect(JSON.stringify(parsed)).not.toContain("private log content");
    expect(parsed.summary.metrics).not.toHaveProperty("hidden_count");
  });
});
