import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import EventRecordCurationPanel from "./EventRecordCurationPanel.vue";


afterEach(() => vi.unstubAllGlobals());

function response(value: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(value),
  });
}

describe("event record curation panel", () => {
  it("previews and applies an explicit audited decision", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({
        id: "candidate-1",
        sourceTitle: "Akt 20/1847",
        eventGroup: "marriage",
        status: "proposed",
        fingerprint: "fingerprint-1",
        stale: false,
        canApply: true,
        latestDecision: null,
        members: [
          { id: "event-a", event_type: "marriage", date_from: "1847-02-09", participant_count: 7 },
          { id: "event-b", event_type: "małżeństwo", date_from: "1847-02-09", participant_count: 7 },
        ],
        impact: { canonicalEventsChanged: 0, sourceRowsDeleted: 0, affectedViews: ["akt"] },
      }))
      .mockImplementationOnce(() => response({ status: "resolved_same_record" }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "command-1" });
    const wrapper = mount(EventRecordCurationPanel, {
      props: {
        candidateId: "candidate-1",
        access: { available: true, authenticated: true, strict: false, csrf: "csrf-1" },
      },
    });

    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Decyzja kuratora");
    expect(wrapper.text()).toContain("Nie usuwa ani nie nadpisuje");

    await wrapper.get<HTMLInputElement>('input[value="same_record"]').setValue(true);
    await wrapper.get("textarea").setValue("Warianty techniczne tego samego aktu numer 20/1847.");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const request = fetchMock.mock.calls[1][1] as RequestInit;
    expect(request.headers).toMatchObject({ "X-CSRF-Token": "csrf-1" });
    expect(JSON.parse(String(request.body))).toMatchObject({
      decision: "same_record",
      expectedFingerprint: "fingerprint-1",
      commandId: "command-1",
    });
    expect(wrapper.emitted("resolved")).toHaveLength(1);
  });
});
