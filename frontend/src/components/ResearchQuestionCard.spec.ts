import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { parseResearchQuestionAssessment, researchAssessmentDispositionLabels } from "../types/presentation";
import type { ResearchBacklogItem, ResearchQuestionAssessment } from "../types/presentation";
import ResearchQuestionCard from "./ResearchQuestionCard.vue";

function assessment(): ResearchQuestionAssessment {
  return {
    schemaVersion: 1,
    disposition: "defer_specific_material",
    missingEvidence: "Brakuje własnej cechy identyfikującej.",
    nextAction: "Zachować odroczenie do otrzymania materiału.",
    returnCondition: "Nowy materiał zawiera własną cechę.",
    source: { logEntryId: "log-test", assessmentVersion: 69, assessedAt: "2026-10-14T10:00:00+02:00", summarySha256: "a".repeat(64) },
    scopedWork: { id: "work-test", itemType: "waiting", status: "blocked", scopeId: "scope-test", parentWorkItemId: null },
  };
}

function item(): ResearchBacklogItem {
  return { id: "question-test", origin: "curated", caseId: "case-test", caseTitle: "Sprawa", question: "Oryginalne pytanie?", completionCriterion: "Oryginalne kryterium", answerStatus: "open", workflowStage: "ready", priority: 80, progress: { score: 55, gates: ["Generyczna bramka"] }, nextAction: "Stary krok" };
}

describe("ResearchQuestionCard assessment", () => {
  it.each(Object.keys(researchAssessmentDispositionLabels) as Array<ResearchQuestionAssessment["disposition"]>)("uses the typed %s label without changing original question status", (disposition) => {
    const current = { ...assessment(), disposition };
    const wrapper = mount(ResearchQuestionCard, { props: { item: { ...item(), assessment: current } } });
    const panel = wrapper.get(".question-assessment");
    expect(panel.text()).toContain(researchAssessmentDispositionLabels[disposition]);
    expect(panel.text()).toContain(current.missingEvidence);
    expect(panel.text()).toContain(current.nextAction);
    expect(panel.text()).toContain(current.returnCondition);
    expect(panel.text()).toContain("oczekuje · zablokowane");
    expect(panel.attributes("data-work-id")).toBe("work-test");
    expect(wrapper.text()).toContain("Oryginalne pytanie?");
    expect(wrapper.text()).toContain("Oryginalne kryterium");
    expect(wrapper.text()).toContain("Pytanie: otwarte");
    expect(wrapper.text()).not.toMatch(/55%|Generyczna bramka|Stary krok|odpowiedziane|potwierdzone/);
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
  });

  it("keeps legacy progress when assessment is absent, without reading verdicts from text", () => {
      const wrapper = mount(ResearchQuestionCard, { props: { item: { ...item(), question: "Confirmed answered source_supported_probable?" } } });
      expect(wrapper.get('[role="progressbar"]').attributes("aria-valuenow")).toBe("55");
      expect(wrapper.text()).toContain("Stary krok");
      expect(wrapper.find(".question-assessment").exists()).toBe(false);
  });

  it.each([null, { ...assessment(), disposition: "Confirmed" }])("fails safely for unavailable or malformed optional assessment %# instead of displaying a generic readiness claim", (value) => {
    const wrapper = mount(ResearchQuestionCard, { props: { item: { ...item(), assessment: value as ResearchQuestionAssessment | null } } });
    expect(wrapper.text()).toContain("Utrwalona ocena jest niedostępna");
    expect(wrapper.text()).not.toMatch(/55%|Generyczna bramka|Stary krok|Confirmed/);
  });
});

describe("assessment projection runtime guard", () => {
  it.each([
    null, [], "ready", { ...assessment(), schemaVersion: 2 }, { ...assessment(), disposition: "toString" },
    { ...assessment(), missingEvidence: {} }, { ...assessment(), nextAction: "" }, { ...assessment(), returnCondition: null },
    { ...assessment(), source: null }, { ...assessment(), source: { ...assessment().source, assessmentVersion: 1 } },
    { ...assessment(), source: { ...assessment().source, assessmentVersion: "69" } },
    { ...assessment(), source: { ...assessment().source, assessedAt: "not a date" } },
    { ...assessment(), source: { ...assessment().source, summarySha256: "broken" } },
    { ...assessment(), scopedWork: [] }, { ...assessment(), scopedWork: { ...assessment().scopedWork, scopeId: "../bad" } },
    { ...assessment(), scopedWork: { ...assessment().scopedWork, parentWorkItemId: undefined } },
  ])("rejects malformed projection %#", (value) => expect(parseResearchQuestionAssessment(value)).toBeNull());

  it("accepts both supported envelope versions and preserves backing IDs", () => {
    for (const version of [60, 69] as const) {
      const value = { ...assessment(), source: { ...assessment().source, assessmentVersion: version }, scopedWork: { ...assessment().scopedWork, parentWorkItemId: "work-parent" } };
      expect(parseResearchQuestionAssessment(value)).toBe(value);
    }
  });
});
