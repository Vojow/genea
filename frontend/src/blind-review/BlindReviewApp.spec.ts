import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  freezeField: vi.fn(),
  loadReviewSession: vi.fn(),
  newCommandId: vi.fn(() => "command-test"),
  revealField: vi.fn(),
  sha256Literal: vi.fn(async () => "b".repeat(64)),
  submitField: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, ...api };
});

import BlindReviewApp from "./BlindReviewApp.vue";


beforeEach(() => {
  window.history.replaceState({}, "", "/blind-review/run-test");
  sessionStorage.clear();
  api.freezeField.mockReset().mockResolvedValue(undefined);
  api.revealField.mockReset().mockResolvedValue(undefined);
  api.submitField.mockReset().mockResolvedValue(undefined);
  api.loadReviewSession.mockReset().mockResolvedValue({
    runId: "run-test",
    csrfToken: "csrf-test",
    fields: [{
      id: "f001",
      kind: "person-name",
      visibleScale: "field",
      frozenScales: [],
      submittedScales: [],
      availableScales: ["field"],
      expectedValue: "Sekretna oczekiwana wartość",
      locator: "Sekretny locator",
      sourceId: "source-secret",
    }],
  });
});

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("blind review application", () => {
  it("renders Polish labels for allowed field kinds without exposing raw kind values", async () => {
    api.loadReviewSession.mockResolvedValueOnce({
      runId: "run-test",
      csrfToken: "csrf-test",
      fields: [
        {
          id: "f001",
          kind: "person-name",
          visibleScale: "field",
          frozenScales: [],
          submittedScales: [],
          availableScales: ["field"],
        },
        {
          id: "f002",
          kind: "other",
          visibleScale: "field",
          frozenScales: [],
          submittedScales: [],
          availableScales: ["field"],
        },
      ],
    });
    const wrapper = mount(BlindReviewApp);
    await flushPromises();

    const headings = wrapper.findAll(".card-header h2").map((heading) => heading.text());
    expect(headings).toEqual(["imię lub nazwisko", "fragment tekstu"]);
    expect(wrapper.text()).not.toContain("person-name");
    wrapper.unmount();
  });

  it("keeps the literal locally and gates context behind the frozen field hash", async () => {
    const wrapper = mount(BlindReviewApp);
    await flushPromises();

    expect(wrapper.text()).not.toContain("Sekretna oczekiwana wartość");
    expect(wrapper.text()).not.toContain("Sekretny locator");
    expect(wrapper.text()).not.toContain("source-secret");
    expect(wrapper.get("img").attributes("src")).toContain("/f001/field");
    expect(wrapper.text()).not.toContain("Odsłoń: szerszy kontekst");

    const input = wrapper.get<HTMLTextAreaElement>("textarea");
    await input.setValue("Dopierała literalnie");
    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();

    expect(api.sha256Literal).toHaveBeenCalledWith("Dopierała literalnie");
    expect(api.freezeField).toHaveBeenCalledWith(
      "f001", "field", "b".repeat(64), "csrf-test", "command-test",
    );
    expect(api.submitField).toHaveBeenCalledWith(
      "f001", "field", "Dopierała literalnie", "csrf-test", "command-test",
    );
    expect(JSON.parse(sessionStorage.getItem("genea:blind-review:run-test") || "{}"))
      .toEqual({ f001: { field: "Dopierała literalnie" } });
    expect(wrapper.text()).toContain("Odsłoń: szerszy kontekst");

    await wrapper.get(".secondary-button").trigger("click");
    await flushPromises();
    expect(api.revealField).toHaveBeenCalledWith("f001", "context", "csrf-test", "command-test");
    expect(wrapper.get("img").attributes("src")).toContain("/f001/context");
    expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("");
    wrapper.unmount();
  });

  it("fails closed when the route and session run IDs differ", async () => {
    api.loadReviewSession.mockResolvedValueOnce({
      runId: "different-run",
      csrfToken: "csrf-test",
      fields: [],
    });
    const wrapper = mount(BlindReviewApp);
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("Sesja zablokowana");
    expect(wrapper.text()).toContain("Identyfikator sesji nie zgadza się");
    expect(wrapper.find(".review-list").exists()).toBe(false);
    wrapper.unmount();
  });

  it("retries only submission when the hash was already frozen", async () => {
    api.submitField.mockRejectedValueOnce(new Error("submission unavailable"));
    const wrapper = mount(BlindReviewApp);
    await flushPromises();
    await wrapper.get("textarea").setValue("Stały odczyt");
    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();

    expect(wrapper.get<HTMLTextAreaElement>("textarea").element.disabled).toBe(true);
    expect(wrapper.get(".primary-button").text()).toContain("Przekaż zamrożony odczyt");
    expect(wrapper.text()).not.toContain("Odsłoń: szerszy kontekst");

    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();
    expect(api.freezeField).toHaveBeenCalledTimes(1);
    expect(api.submitField).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Odsłoń: szerszy kontekst");
    wrapper.unmount();
  });

  it("locks an incomplete frozen stage when its local literal is unavailable", async () => {
    api.loadReviewSession.mockResolvedValueOnce({
      runId: "run-test",
      csrfToken: "csrf-test",
      fields: [{
        id: "f001",
        kind: "person-name",
        visibleScale: "field",
        frozenScales: ["field"],
        submittedScales: [],
        availableScales: ["field"],
      }],
    });
    const wrapper = mount(BlindReviewApp);
    await flushPromises();

    expect(wrapper.text()).toContain("Tego etapu nie można bezpiecznie wznowić");
    expect(wrapper.get<HTMLButtonElement>(".primary-button").element.disabled).toBe(true);
    expect(wrapper.get<HTMLTextAreaElement>("textarea").element.disabled).toBe(true);
    wrapper.unmount();
  });
});
