import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, describe, expect, it, vi } from "vitest";

import ResearchWorkspaceView from "./ResearchWorkspaceView.vue";
import { createTestRouter } from "../router";
import { useAppStore } from "../stores/app";
import type { PresentationPayload } from "../types/presentation";

const missing = () => ({ value: null, status: "unknown", reason: "unsupported" });
const metric = (value: number | null, status: string, unit: string, unknownObservations = 0) => ({ value, status, unit, observations: 2, unknownObservations });
const telemetryFixture = () => {
  const metrics = {
    input_tokens: metric(100, "actual", "tokens"), cached_input_tokens: metric(20, "actual", "tokens"),
    output_tokens: metric(50, "actual", "tokens"), reasoning_output_tokens: metric(10, "actual", "tokens"),
    cost_usd: metric(0.25, "partial", "usd", 1), hits: metric(4, "estimated", "hits"),
    duration_seconds: metric(40, "actual", "seconds"), accepted_fields: metric(2, "actual", "fields"),
  };
  return {
    schemaVersion: 1, generatedAt: null, status: "ready",
    summary: { eventCount: 1, eventIds: ["event-test"], metrics },
    batches: [{ id: "batch-test", batchId: "batch-test", label: "Partia testowa", eventCount: 1, eventIds: ["event-test"], metrics, workItems: missing() }],
    scopes: ["one", "two"].map((id) => ({ id: `person:${id}`, scope: { kind: "person", id, label: `Zakres ${id}`, availability: "available" }, batchIds: ["batch-test"], eventCount: 1, eventIds: ["event-test"], metrics, workItems: missing() })),
    unsupported: { diskUsage: missing(), archiveCoverage: missing(), wallTime: missing(), workItems: missing() },
  };
};

const wrappers: VueWrapper[] = [];
afterEach(() => { wrappers.splice(0).forEach((wrapper) => wrapper.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function render(path = "/research/dashboard") {
  const pinia = createPinia();
  const store = useAppStore(pinia);
  store.payload = {
    meta: { schemaVersion: 7, generatedAt: "test" }, people: [], sources: [], occurrences: [], events: [], relationships: [], households: [], claims: [], conflicts: [], identityCandidates: [],
  } as unknown as PresentationPayload;
  const router = createTestRouter();
  await router.push(path);
  await router.isReady();
  const wrapper = mount(ResearchWorkspaceView, { global: { plugins: [pinia, router] } });
  wrappers.push(wrapper);
  return { wrapper, router, store };
}

describe("research workspace private telemetry", () => {
  it("shows loading, unknown/partial/estimated/actual and source limitations", async () => {
    let complete!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((resolve) => { complete = resolve; })));
    const { wrapper } = await render();
    expect(wrapper.get('[role="status"]').text()).toContain("Wczytywanie");
    expect(wrapper.get(".telemetry-panel").attributes("aria-busy")).toBe("true");
    complete({ ok: true, json: async () => telemetryFixture() });
    await flushPromises();
    expect(wrapper.text()).toContain("Partia testowa");
    expect(wrapper.get('[data-metric="input_tokens"]').text()).toContain("pomiar (actual)");
    expect(wrapper.get('[data-metric="cost_usd"]').text()).toContain("znany subtotal (partial)");
    expect(wrapper.get('[data-metric="cost_usd"]').text()).toContain("Brak pomiaru w 1 obserwacjach");
    expect(wrapper.get('[data-metric="hits"]').text()).toContain("Trafienia: 4 · estymacja (estimated)");
    expect(wrapper.get('[data-metric="pages"]').text()).toContain("brak danych (unknown)");
    expect(wrapper.get('[data-metric="reasoning_output_tokens"]').text()).toContain("część wyjścia");
    expect(wrapper.get(".telemetry-sources").text()).toContain("Pokrycie archiwumbrak źródła (unknown)");
    expect(wrapper.get(".telemetry-row").text()).toContain("Brak źródła blockerów");
  });

  it("switches scope cards without summing shared events or persisting telemetry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => telemetryFixture() }));
    const { wrapper, store } = await render();
    const before = JSON.stringify(store.payload);
    const persistence = vi.spyOn(Storage.prototype, "setItem");
    await flushPromises();
    await wrapper.get('input[value="scopes"]').setValue();
    expect(wrapper.findAll(".telemetry-row")).toHaveLength(2);
    expect(wrapper.get(".telemetry-summary").text()).toContain("Unikalne zdarzenia: 1");
    expect(wrapper.text()).toContain("Wspólny nakład powiązanych operacji");
    expect(wrapper.findAll(".telemetry-audit")[0]?.text()).toContain("event-test");
    expect(JSON.stringify(store.payload)).toBe(before);
    expect(persistence).not.toHaveBeenCalled();
  });

  it("shows an empty state without a fabricated zero cost", async () => {
    const fixture = telemetryFixture();
    Object.assign(fixture, { status: "empty", summary: { eventCount: 0, eventIds: [], metrics: {} }, scopes: [], batches: [] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => fixture }));
    const { wrapper } = await render();
    await flushPromises();
    expect(wrapper.text()).toContain("Brak dopuszczonych danych telemetrii");
    expect(wrapper.text()).not.toContain("0 USD");
  });

  it("clears stale telemetry on refresh failure and supports retry", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => telemetryFixture() })
      .mockRejectedValueOnce(new Error("private transport content"))
      .mockResolvedValueOnce({ ok: true, json: async () => telemetryFixture() });
    vi.stubGlobal("fetch", fetch);
    const { wrapper } = await render();
    await flushPromises();
    await wrapper.get(".telemetry-panel button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Nie udało się pobrać");
    expect(wrapper.text()).not.toContain("private transport content");
    expect(wrapper.findAll(".telemetry-row")).toHaveLength(0);
    await wrapper.get(".telemetry-panel button").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".telemetry-row")).toHaveLength(1);
  });

  it("fetches only on the dashboard and cancels in-flight data when leaving", async () => {
    const fetch = vi.fn().mockReturnValue(new Promise(() => undefined));
    vi.stubGlobal("fetch", fetch);
    const { wrapper, router } = await render("/research/cases");
    expect(fetch).not.toHaveBeenCalled();
    await router.push("/research/dashboard");
    await flushPromises();
    expect(fetch).toHaveBeenCalledTimes(1);
    const signal = fetch.mock.calls[0]?.[1].signal as AbortSignal;
    await router.push("/research/cases");
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.find(".telemetry-panel").exists()).toBe(false);
  });
});
