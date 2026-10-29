import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { createPinia } from "pinia";

import type { PersonIdentityCandidate } from "../../models/person-profile-projector";
import { createTestRouter, entityPath } from "../../router";
import EntityLink from "../../components/EntityLink.vue";
import PersonResearchState from "./PersonResearchState.vue";

function candidate(overrides: Partial<PersonIdentityCandidate> = {}): PersonIdentityCandidate {
  return {
    id: "candidate", fromOccurrenceId: "occ-profile", toOccurrenceId: "occ-act",
    fromLabel: "Profil", toLabel: "Stanisław", status: "unresolved", score: 0.9,
    basis: '{"support":["raw JSON"]}', hasStructuredBasis: true,
    support: ["Zgodne miejsce"], against: ["Brak pełnej daty"], humanReviewDebt: ["Sprawdź skan"],
    sourceLinks: [{ sourceId: "source-act", sourceLabel: "Akt kandydacki", occurrenceId: "occ-act" }],
    ...overrides,
  };
}

async function render(item: PersonIdentityCandidate) {
  const router = createTestRouter();
  await router.push("/tree");
  return mount(PersonResearchState, {
    props: { candidates: [item], conflicts: [], questions: [], unresolvedFacts: [] },
    global: { plugins: [createPinia(), router] },
  });
}

describe("PersonResearchState candidate presentation", () => {
  it("renders typed reasons and a real source link with occurrence context, retaining unresolved semantics", async () => {
    const wrapper = await render(candidate());
    expect(wrapper.findAll("h4").map((heading) => heading.text())).toEqual([
      "Przesłanki wspierające", "Ograniczenia i przesłanki przeciw", "Do kontroli człowieka",
    ]);
    expect(wrapper.findAll("li").map((item) => item.text())).toEqual(["Zgodne miejsce", "Brak pełnej daty", "Sprawdź skan"]);
    expect(wrapper.text()).not.toContain("raw JSON");
    expect(wrapper.text()).not.toContain('"support"');
    expect(wrapper.text()).toContain("nierozstrzygnięte");
    expect(wrapper.text()).toContain("Priorytet badawczy, nie prawdopodobieństwo");
    expect(wrapper.get("a").attributes("href")).toBe(entityPath("source", "source-act"));
    expect(wrapper.getComponent(EntityLink).props()).toEqual(expect.objectContaining({
      sourceId: "source-act", occurrenceId: "occ-act", label: "Akt kandydacki",
    }));
    wrapper.unmount();
  });

  it("renders the legacy plain basis when structured fields are absent and no links without visible sources", async () => {
    const wrapper = await render(candidate({
      hasStructuredBasis: false, support: undefined, against: undefined, humanReviewDebt: undefined,
      basis: "Zbieżność zapisów wymaga kontroli.", sourceLinks: [],
    }));
    expect(wrapper.text()).toContain("Zbieżność zapisów wymaga kontroli.");
    expect(wrapper.find("h4").exists()).toBe(false);
    expect(wrapper.find("a").exists()).toBe(false);
    wrapper.unmount();
  });

  it("retains legacy justification when the export supplies empty structured arrays", async () => {
    const wrapper = await render(candidate({
      hasStructuredBasis: false, support: [], against: [], humanReviewDebt: [], sourceLinks: [],
      basis: "Starsza zapisana podstawa pozostaje do sprawdzenia.",
    }));
    expect(wrapper.text()).toContain("Starsza zapisana podstawa pozostaje do sprawdzenia.");
    expect(wrapper.find("h4").exists()).toBe(false);
    wrapper.unmount();
  });
});
