import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { PersonOccurrenceEntry } from "../../models/person-profile-projector";
import PersonOccurrenceCard from "./PersonOccurrenceCard.vue";


function entry(overrides: Partial<PersonOccurrenceEntry> = {}): PersonOccurrenceEntry {
  return {
    id: "occ-local-a",
    rawName: "Jan Testowy",
    normalizedName: "Jan Testowy",
    role: "witness",
    status: "agent_approved",
    confidence: null,
    eventId: null,
    eventType: "marriage",
    eventDate: "1880-01-01",
    year: 1880,
    placeId: null,
    placeLabel: "",
    sourceId: null,
    sourceLabel: "",
    reading: null,
    conflictIds: [],
    componentPersonId: "person-local-a",
    componentLabel: "Jan Testowy",
    identityDecisionTier: null,
    identityDecisionLabel: null,
    humanIdentityReviewOpen: false,
    uncertain: false,
    ...overrides,
  };
}

describe("PersonOccurrenceCard", () => {
  it("shows the explicit open human review only for an occurrence carrying the agent tier", () => {
    const pending = mount(PersonOccurrenceCard, {
      props: {
        entry: entry({
          identityDecisionTier: "agent_approved_human_pending",
          identityDecisionLabel: "tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka",
          humanIdentityReviewOpen: true,
        }),
      },
    });

    expect(pending.get(".person-occurrence-identity-review").text()).toContain("tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka");
    expect(pending.get(".person-occurrence-identity-review").text()).toContain("Kontrola człowieka: otwarta");
    expect(pending.get(".person-occurrence-identity-review").text()).toContain("occ-local-a");

    const withoutTier = mount(PersonOccurrenceCard, { props: { entry: entry({ rawName: "Jan Testowy" }) } });
    expect(withoutTier.find(".person-occurrence-identity-review").exists()).toBe(false);
  });

  it("shows a human-confirmed occurrence with the human review closed", () => {
    const confirmed = mount(PersonOccurrenceCard, {
      props: {
        entry: entry({
          identityDecisionTier: "human_confirmed",
          identityDecisionLabel: "Tożsamość potwierdzona przez człowieka",
          humanIdentityReviewOpen: false,
        }),
      },
    });

    expect(confirmed.get(".person-occurrence-identity-review").text()).toContain("Tożsamość potwierdzona przez człowieka");
    expect(confirmed.get(".person-occurrence-identity-review").text()).toContain("Kontrola człowieka: zamknięta");
    expect(confirmed.classes()).not.toContain("has-open-identity-review");
  });
});
