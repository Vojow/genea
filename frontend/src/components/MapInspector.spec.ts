import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import { createTestRouter } from "../router";
import type { MapInspection } from "../types/presentation";
import MapInspector from "./MapInspector.vue";


afterEach(() => {
  document.body.replaceChildren();
  sessionStorage.clear();
});

describe("MapInspector source observations", () => {
  it("shows source backing data without a person link or line assignment", async () => {
    document.body.innerHTML = '<aside id="map-inspector"></aside>';
    const router = createTestRouter();
    await router.push("/map");
    const inspection: MapInspection = {
      place: { id: "place-dabrowa", label: "Dąbrowa" },
      lines: [],
      items: [
        {
          id: "person-observation",
          placeId: "place-dabrowa",
          personId: "person-known",
          personLabel: "Osoba jawnie przypisana",
          occurrenceId: "occ-known",
          eventId: "event-known",
          eventType: "birth",
          sourceId: "source-known",
          role: "child",
          dateFrom: "1900-01-03",
        },
        {
          id: "source-observation",
          placeId: "place-dabrowa",
          occurrenceId: "occ-source-only",
          occurrenceLabel: "Marianna Kowalska",
          eventId: "event-source-only",
          eventType: "marriage",
          sourceId: "source-source-only",
          role: "witness",
          dateFrom: "1835-02-15",
        },
      ],
    };

    const wrapper = mount(MapInspector, {
      props: { inspection, sourceIds: new Set(["source-known", "source-source-only"]) },
      global: { plugins: [router], stubs: { Teleport: true } },
    });

    expect(wrapper.get(".inspector-subtitle").text()).toContain("1 obserwacja osoby · 1 wystąpienie źródłowe");
    const sourceCard = wrapper.get(".map-observation-card.is-source-only");
    expect(sourceCard.get("strong").text()).toBe("Marianna Kowalska");
    expect(sourceCard.text()).toContain("occ-source-only");
    expect(sourceCard.text()).toContain("15 lutego 1835");
    expect(sourceCard.text()).toContain("świadek");
    expect(sourceCard.text()).toContain("Zdarzenie: event-source-only");
    expect(sourceCard.text()).toContain("Źródło: source-source-only");
    expect(sourceCard.find("a.entity-link-person").exists()).toBe(false);
    expect(sourceCard.get("a.entity-link-source").text()).toContain("Otwórz akt i transkrypcję");
    expect(wrapper.findAll("a.entity-link-person")).toHaveLength(1);
    expect(wrapper.find(".inspector-kind .badge").exists()).toBe(false);
    wrapper.unmount();
  });
});
