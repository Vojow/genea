import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import type { TreeInspectorModel } from "../types/presentation";
import TreeInspector from "./TreeInspector.vue";
import { familyKnowledgePresentation } from "../../core/family-knowledge.mjs";


afterEach(() => {
  document.body.replaceChildren();
  sessionStorage.clear();
});

function externalInspection(): TreeInspectorModel {
  return {
    personId: "profile-public",
    identityScope: "presentation_only",
    identityLabel: "Zewnętrzny profil kandydacki",
    identityNotice: "Tożsamość wymaga rozstrzygnięcia. Relacje pozostają niekanoniczne.",
    identityComponentCount: 0,
    generation: "Linia poboczna",
    evidence: "imported_unverified",
    evidenceKind: "is-warning",
    name: "Emilia z drzewa zewnętrznego",
    fullRecordHref: "/people/profile-public?returnView=tree&network=network-public&person=profile-public&candidateRole=collateral",
    summary: "Klasyfikacja source-reported.",
    lifeSpanLabel: "?–?",
    review: null,
    structureActions: [],
    family: { parents: [], coParents: [], children: [], siblings: [] },
    unions: [],
    sourceRelations: [],
    candidates: [],
    appearances: [],
    appearanceSummary: "0 aktów · 0 wystąpień",
    facts: [],
    materials: [],
    unavailableCount: 0,
    materialTitle: "Podstawa i materiały",
    materialNote: "Restricted provenance nie jest publikowane.",
    importantFacts: [],
    alerts: [],
    uniqueSources: [],
    tabs: [
      { id: "summary", label: "Podsumowanie", count: 0 },
      { id: "family", label: "Rodzina", count: 0 },
      { id: "acts", label: "Akty", count: 0 },
    ],
  };
}

describe("TreeInspector", () => {
  it("renders recognition with user attribution alongside unresolved identity", () => {
    document.body.innerHTML = '<aside id="tree-inspector"></aside><div id="app"></div>';
    const inspection = externalInspection();
    inspection.familyKnown = familyKnowledgePresentation({
      status: "family_known", provenanceKind: "family_testimony", basis: "user_reported_family_conversations",
      confidenceDeclared: "100%", confidenceSemantics: "user_declared", recognitionScope: "family_person",
      provenanceRef: "opaque-family-digest", informantId: "PRIVATE-INFORMANT",
    });
    const wrapper = mount(TreeInspector, { attachTo: "#app", props: { inspection } });
    const notice = document.querySelector(".tree-family-known-notice");
    expect(notice?.textContent).toContain("Znany rodzinnie");
    expect(notice?.textContent).toMatch(/100%.*użytkownika.*rozmów rodzinnych/);
    expect(document.querySelector(".tree-identity-notice")?.textContent).toContain("Tożsamość wymaga rozstrzygnięcia");
    expect(document.querySelector("#tree-inspector")?.innerHTML).not.toMatch(/PRIVATE-INFORMANT|opaque-family-digest/);
    wrapper.unmount();
  });

  it("does not fabricate a person profile for an unmaterialized conditional occurrence", () => {
    document.body.innerHTML = '<aside id="tree-inspector"></aside><div id="app"></div>';
    const inspection = externalInspection();
    inspection.fullRecordHref = null;
    inspection.suppressFullRecordLink = true;
    inspection.identityLabel = "Warunkowe wystąpienie źródłowe";
    const wrapper = mount(TreeInspector, { attachTo: "#app", props: { inspection } });
    expect(document.querySelectorAll("#tree-inspector a")).toHaveLength(0);
    expect(document.querySelector("#tree-inspector")?.textContent).toContain("Warunkowe wystąpienie źródłowe");
    wrapper.unmount();
  });

  it("renders the only external-profile navigation as an explicit privacy-safe full-card link", () => {
    document.body.innerHTML = '<aside id="tree-inspector"></aside><div id="app"></div>';
    const wrapper = mount(TreeInspector, {
      attachTo: "#app",
      props: { inspection: externalInspection() },
    });

    const link = document.querySelector<HTMLAnchorElement>("#tree-inspector .tree-external-profile-link");
    expect(link?.textContent).toContain("Otwórz pełną kartę · Emilia z drzewa zewnętrznego");
    expect(link?.getAttribute("href")).toBe("/people/profile-public?returnView=tree&network=network-public&person=profile-public&candidateRole=collateral");
    expect(document.querySelectorAll("#tree-inspector a")).toHaveLength(1);
    expect(document.querySelector("#tree-inspector")?.innerHTML).not.toMatch(/person-private|occurrence-private|source_item_|assertion_/i);

    wrapper.unmount();
  });

  it("renders mixed local occurrence reviews without replacing person identity", () => {
    document.body.innerHTML = '<aside id="tree-inspector"></aside><div id="app"></div>';
    const inspection = externalInspection();
    inspection.identityScope = "canonical";
    inspection.identityLabel = "Osoba kanoniczna";
    inspection.localIdentityReviews = [
      {
        decisionTier: "agent_approved_human_pending",
        label: "tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka",
        humanReviewLabel: "Kontrola człowieka: otwarta",
        humanReviewOpen: true,
        occurrenceIds: ["occ-local-a"],
      },
      {
        decisionTier: "human_confirmed",
        label: "Tożsamość potwierdzona przez człowieka",
        humanReviewLabel: "Kontrola człowieka: zamknięta",
        humanReviewOpen: false,
        occurrenceIds: ["occ-local-confirmed"],
      },
    ];
    const wrapper = mount(TreeInspector, { attachTo: "#app", props: { inspection } });

    const reviews = [...document.querySelectorAll<HTMLDetailsElement>("#tree-inspector .tree-local-identity-review")];
    expect(reviews).toHaveLength(2);
    expect(reviews[0].open).toBe(true);
    expect(reviews[0].textContent).toContain("Kontrola człowieka: otwarta");
    expect(reviews[0].textContent).toContain("occ-local-a");
    expect(reviews[1].open).toBe(false);
    expect(reviews[1].textContent).toContain("Kontrola człowieka: zamknięta");
    expect(reviews[1].textContent).toContain("Tożsamość potwierdzona przez człowieka");
    expect(reviews[1].textContent).toContain("occ-local-confirmed");
    expect(reviews[1].textContent).toContain("nie zmienia osoby ani relacji");
    expect(document.querySelector("#tree-inspector .inspector-kind")?.textContent).toContain("Osoba kanoniczna");

    wrapper.unmount();
  });
});
