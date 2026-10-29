import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { PresentationSource } from "../types/presentation";
import SourceReviewPanel from "./SourceReviewPanel.vue";

const source: PresentationSource = {
  id: "source-birth",
  title: "Akt urodzenia Aleksandra Testowego",
  readings: [],
  comparisons: [{
    id: "comparison",
    blindVersionId: "blind-v1",
    contextualVersionId: "contextual-v1",
    status: "draft",
    differences: [{
      field: "mother.surname",
      blind: "[M…ka?]",
      contextual: "Kubiaków",
      literalReading: "[M…ka?]",
      comparisonEvidence: "Inicjał przypomina M.",
      hypothesis: "Kubiaków pozostaje hipotezą kontekstową.",
      identityDecision: "Brak decyzji.",
    }],
  }],
  reviewWorkflow: {
    id: "review",
    revision: 1,
    status: "human_review_required",
    decidedByKind: "agent",
    rationale: "Jedno pole wymaga kontroli.",
    fields: [{ field: "father_age", status: "human_review_required", value: "lat 42", alternative: "lat 22" }],
    summary: { inputCount: 3, blindInputCount: 2, agentInputCount: 3, fieldCount: 1, agentApprovedFieldCount: 0, humanReviewRequiredFieldCount: 1, humanReviewedFieldCount: 0, hasAgentApproval: true, needsHumanReview: true },
  },
  readingPipelineRuns: [{
    id: "pipeline-run",
    artifactId: "artifact",
    provider: "Scribe-like benchmark",
    model: "v1",
    pipelineVersion: "genea-reading-pipeline-v1",
    documentType: "birth-baptism",
    sourceLanguage: "rus",
    status: "review_required",
    completeness: 0.8,
    commandId: "pipeline-command",
    summary: { fieldCount: 8, humanReviewRequiredFieldCount: 1, missingFieldCount: 1, escalationCount: 1, independentReadingCount: 2, disagreementCount: 3 },
    escalations: [{ id: "issue", fieldPath: "mother.name", reasonCode: "ambiguous_reading", priority: 2, recommendedRole: "agent_adjudicator" }],
    metrics: { estimatedCost: 0.18, durationMs: 18000 },
    verificationCard: {
      schema: "genea.reading-verification-card/v1",
      summary: { fieldCount: 1, agreedCount: 0, disputedCount: 1, incompleteCount: 0, notComparedCount: 0, humanReviewRequiredCount: 1 },
      fields: [{
        path: "mother.name",
        role: "mother",
        criticality: "critical",
        proposal: { value: "[M…ka?]", normalizedValue: null, confidence: 0.5, certainty: "Ambiguous", alternatives: [], materialAlternatives: ["Kubiaków"], sourceQuote: "[M…ka?]" },
        blindReadings: [{ readingId: "blind-v1", readerId: "reader-a", value: "[M…ka?]" }, { readingId: "blind-v2", readerId: "reader-b", value: "Kubiaków" }],
        comparison: { status: "disputed", differenceKind: "different_value", values: [] },
        workflow: { status: "human_review_required", basis: "pipeline_assessment" },
        issues: [],
        backing: { sourceItemId: "source-birth", artifactId: "artifact", sourceSegmentId: "segment-mother", bbox: null },
      }],
      issues: [],
    },
  }],
  coverage: [{
    id: "coverage",
    key: "source-birth:transcription",
    revision: 1,
    dimension: "transcription",
    level: "full_literal",
    scope: "cały akt",
    unitKind: "act",
    completed: 1,
    total: 1,
    status: "complete",
    systematic: true,
    qaStatus: "reviewed",
    basisType: "human_review",
    rationale: "Odczyt obejmuje cały akt.",
  }],
};

describe("SourceReviewPanel", () => {
  function withFields(fields: unknown[]) {
    const typed = structuredClone(source);
    typed.readingPipelineRuns = [];
    typed.reviewWorkflow!.fields = fields as unknown as NonNullable<PresentationSource["reviewWorkflow"]>["fields"];
    return mount(SourceReviewPanel, { props: { source: typed } });
  }

  it.each([
    ["rejected_as_literal", "odrzucony odczyt literalny"],
    ["rejected_as_preferred_literal", "odrzucony jako preferowany odczyt literalny"],
    ["retained_editorial_expansion_only", "wyłącznie rozwinięcie redakcyjne"],
    ["unresolved", "nierozstrzygnięty"],
    ["unresolved_disfavored", "nierozstrzygnięty, słabszy"],
    ["unresolved_literal_spelling", "nierozstrzygnięta pisownia literalna"],
    ["unresolved_unaccepted", "nieprzyjęty, nierozstrzygnięty"],
    ["retained_disfavored", "zachowany, słabszy"],
    ["retained_blindA_notation", "zapis pierwszego odczytu ślepego"],
    ["retained_blindB_notation", "zapis drugiego odczytu ślepego"],
  ])("renders explicit alternative disposition %s independently of field review", (status, label) => {
    const wrapper = withFields([{ path: "act.readingAndSigning", preferredValue: "prze- / czy",
      alternatives: ["przeczytany"], alternativeDispositions: [{ value: "przeczytany", status }],
      rationale: "Notatka odczytu.", reviewStatus: "agent_adjudicated" }]);
    const row = wrapper.get(".review-field-list .review-field");
    expect(row.find("p").text()).toBe("prze- / czy");
    expect(row.attributes("data-review-state")).toBe("agent_adjudicated");
    expect(row.get("small").text()).toBe(`Alternatywa: przeczytany · ${label}`);
    expect(row.get("small").attributes("data-alternative-disposition")).toBe(status);
    expect(row.get(".review-field-rationale").text()).toBe("Uzasadnienie: Notatka odczytu.");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("1 pól dla Ciebie");
  });

  it("preserves S68 literal alternatives and distinguishes rejected, editorial and unresolved readings", () => {
    const wrapper = withFields([
      { path: "act.readingAndSigning", preferredValue: "prze- / czy",
        alternatives: ["przeczytany", "prze-[czytany?]"],
        alternativeDispositions: [{ value: "przeczytany", status: "rejected_as_literal" },
          { value: "prze-[czytany?]", status: "retained_editorial_expansion_only" }],
        rationale: "Brak widocznego tany.", reviewStatus: "agent_adjudicated" },
      { path: "signatory.signature", preferredValue: "Ks. M[nieczytelne]",
        alternatives: ["Ks. M. Mrozowski"],
        alternativeDispositions: [{ value: "Ks. M. Mrozowski", status: "unresolved_unaccepted" }],
        rationale: "Granica inicjału i nazwiska pozostaje niepewna.", reviewStatus: "human_review_required" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    expect(rows[0].findAll("small").map((item) => item.text())).toEqual([
      "Alternatywa: przeczytany · odrzucony odczyt literalny",
      "Alternatywa: prze-[czytany?] · wyłącznie rozwinięcie redakcyjne",
    ]);
    expect(rows[1].get("small").text()).toBe("Alternatywa: Ks. M. Mrozowski · nieprzyjęty, nierozstrzygnięty");
    expect(rows[1].find("p").text()).toBe("Ks. M[nieczytelne]");
    expect(rows[1].attributes("data-review-state")).toBe("human_review_required");
  });

  it("keeps legacy, malformed, conflicting and unknown alternative dispositions unknown without prose inference", () => {
    const wrapper = withFields([
      { path: "legacy", value: "literal", alternative: "legacy", rationale: "legacy odrzucony jako odczyt literalny",
        reviewStatus: "agent_consensus" },
      ...[null, {}, "rejected_as_literal", [{ value: "variant", status: {} }],
        [{ value: "variant", status: "future_status" }],
        [{ value: "variant", status: "rejected_as_literal" }, { value: "variant", status: "unresolved" }],
        [{ value: "variant", status: "rejected_as_literal" }, { value: "variant" }],
      ].map((alternativeDispositions) => ({ path: "malformed", preferredValue: "literal", alternatives: ["variant"],
        alternativeDispositions, rationale: { private: "PRIVATE-EXTRA" }, reviewStatus: "agent_consensus" })),
      { path: "safe", preferredValue: 0, alternatives: [0, '<img src=x onerror="PRIVATE-SCRIPT">'],
        alternativeDispositions: [{ value: 0, status: "unresolved" },
          { value: { private: "PRIVATE-EXTRA" }, status: "rejected_as_literal" }, null],
        rationale: '<img src=x onerror="PRIVATE-SCRIPT">', reviewStatus: "agent_single_pass" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    rows.slice(0, -1).forEach((row) => {
      expect(row.get("small").attributes("data-alternative-disposition")).toBe("unknown");
      expect(row.get("small").text()).toContain("stan alternatywy nieustalony");
      expect(row.attributes("data-review-state")).toBe("agent_consensus");
    });
    expect(rows[0].get(".review-field-rationale").text()).toContain("legacy odrzucony jako odczyt literalny");
    rows.slice(1, -1).forEach((row) => expect(row.find(".review-field-rationale").exists()).toBe(false));
    expect(rows.at(-1)!.find("p").text()).toBe("0");
    expect(rows.at(-1)!.get('[data-alternative-disposition="unresolved"]').text()).toContain("Alternatywa: 0");
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.html()).not.toMatch(/PRIVATE-EXTRA|future_status/);
  });

  it("renders preferredValue-only B58 fields without filling absent readings or changing review status", () => {
    const wrapper = withFields([
      { path: "act.number", preferredValue: "58", reviewStatus: "agent_consensus" },
      { path: "mother.birthFamilyNameLiteral", preferredValue: "z Bolewaczów", certainty: "Ambiguous",
        alternatives: ["z Polewaczów"], reviewStatus: "human_review_required" },
      { path: "child.surnameLiteral", preferredValue: null,
        contextValue: "DO-NOT-FILL", reviewStatus: "agent_adjudicated" },
      { path: "unknown.field_name", preferredValue: 0, reviewStatus: "agent_single_pass" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    expect(rows[0].find("strong").text()).toBe("numer aktu");
    expect(rows[0].find("p").text()).toBe("58");
    expect(rows[0].attributes("data-review-state")).toBe("agent_consensus");
    expect(rows[1].find("strong").text()).toBe("matka · nazwisko rodowe w źródle");
    expect(rows[1].find("p").text()).toBe("z Bolewaczów");
    expect(rows[1].text()).toContain("Alternatywa: z Polewaczów");
    expect(rows[1].attributes("data-review-state")).toBe("human_review_required");
    expect(rows[2].find("p").text()).toBe("wartość nieustalona");
    expect(rows[2].attributes("data-review-state")).toBe("agent_adjudicated");
    expect(rows[3].find("strong").text()).toBe("unknown · field name");
    expect(rows[3].find("p").text()).toBe("0");
    expect(wrapper.html()).not.toContain("DO-NOT-FILL");
  });

  it("presents the three explicit B58 NotRecorded observations with their original code and rationale", () => {
    const fields = [
      { alternatives: [], certainty: "NotRecorded", path: "mother.marriedSurnameLiteral", preferredValue: null,
        rationale: "Nie zapisano odrębnego nazwiska matki po ślubie. Jest to obserwacja braku pola, nie odczyt o pewności Certain.",
        reviewStatus: "agent_adjudicated", segmentIds: ["C08", "C09"] },
      { alternatives: [], certainty: "NotRecorded", path: "mother.residenceLiteral", preferredValue: null,
        rationale: "Brak odrębnej frazy określającej zamieszkanie matki; Gaju z opisu urodzenia ani stawającego nie przenoszę do tego pola.",
        reviewStatus: "agent_adjudicated", segmentIds: ["C08", "C09"] },
      { alternatives: [], certainty: "NotRecorded", path: "child.surnameLiteral", preferredValue: null,
        rationale: "Nadano wyłącznie imię. Nazwisko stawającego ani pisownia indeksowa nie uzupełniają literalnego pola nazwiska dziecka.",
        reviewStatus: "agent_adjudicated", segmentIds: ["C10", "C11"] },
    ];
    const wrapper = withFields(fields);
    wrapper.findAll(".review-field-list .review-field").forEach((row) => {
      expect(row.find("p").text()).toBe("Nie zapisano w akcie");
      expect(row.attributes("data-review-state")).toBe("agent_adjudicated");
    });
    const note = wrapper.get(".review-workflow-panel .source-technical-note");
    expect(note.findAll("p").filter((item) => item.text().includes("NotRecorded"))).toHaveLength(3);
    fields.forEach((field) => expect(note.text()).toContain(field.rationale));
    expect(wrapper.get(".review-workflow-stats").text()).toContain("1 pól dla Ciebie");
  });

  it("does not infer NotRecorded absence from null, missing values or conflicting and malformed aliases", () => {
    const wrapper = withFields([
      { path: "child.surnameLiteral", preferredValue: null, reviewStatus: "agent_adjudicated" },
      { path: "child.surnameLiteral", certainty: "NotRecorded", reviewStatus: "agent_adjudicated" },
      { path: "child.surnameLiteral", certainty: "NotRecorded", preferredValue: "", reviewStatus: "agent_adjudicated" },
      { path: "child.surnameLiteral", certainty: "NotRecorded", preferredValue: null, value: "CONFLICT", reviewStatus: "agent_adjudicated" },
      { path: "child.surnameLiteral", certainty: "NotRecorded", preferredValue: null, rawValue: null, reviewStatus: "agent_adjudicated" },
      { path: "child.surnameLiteral", certainty: "NotRecorded", preferredValue: null, value: {}, reviewStatus: "agent_adjudicated" },
      { path: {}, certainty: "NotRecorded", preferredValue: null, reviewStatus: "agent_adjudicated" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    rows.forEach((row) => expect(row.find("p").text()).toBe("wartość nieustalona"));
    rows.slice(0, 3).forEach((row) => expect(row.attributes("data-review-state")).toBe("agent_adjudicated"));
    rows.slice(3).forEach((row) => expect(row.attributes("data-review-state")).toBe("unknown"));
    expect(wrapper.html()).not.toMatch(/Nie zapisano w akcie|NotRecorded|CONFLICT/);
  });

  it("keeps preferredValue alias conflicts, explicit nulls and malformed values unknown", () => {
    const wrapper = withFields([
      { path: "valid", value: "CURRENT", preferredValue: "OTHER", reviewStatus: "agent_consensus" },
      { path: "valid", value: null, preferredValue: "FALLBACK", reviewStatus: "agent_consensus" },
      { path: "valid", rawValue: "RAW", preferredValue: null, reviewStatus: "agent_consensus" },
      ...[{}, [], false, Number.POSITIVE_INFINITY].map((preferredValue) =>
        ({ path: "valid", preferredValue, value: "FALLBACK", reviewStatus: "agent_consensus" })),
      { path: "valid", preferredValue: { private: "PRIVATE-EXTRA" }, reviewStatus: "human_reviewed" },
    ]);
    wrapper.findAll(".review-field-list .review-field").forEach((row) => {
      expect(row.find("p").text()).toBe("wartość nieustalona");
      expect(row.attributes("data-review-state")).toBe("unknown");
    });
    expect(wrapper.html()).not.toMatch(/CURRENT|OTHER|FALLBACK|RAW|PRIVATE-EXTRA|Infinity/);
  });

  it("shows D21 alternatives and absence codes while preserving counts, statuses and literal text", () => {
    const typed = structuredClone(source);
    typed.readingPipelineRuns = [];
    typed.reviewWorkflow!.fields = [
      { path: "motherMaidenFamily", value: "z Polcarów", preferredValue: "z Polcarów", certainty: "Ambiguous",
        alternative: "z Polczarów", alternatives: ["z Polczarów", "z Polenców", "z Polenaców", { private: "PRIVATE-EXTRA" }],
        reviewStatus: "human_review_required" },
      { path: "deceasedOccupation", value: "NotStated", preferredValue: "NotStated", valueKind: "absence_observation",
        certainty: "Certain", reviewStatus: "agent_consensus" },
      { path: "registrationTime", value: "o go. dzinie siódmej wieczorem", preferredValue: "o go. dzinie siódmej wieczorem",
        alternatives: ["o go- dzinie siódmej wieczorem", '<img src=x onerror="PRIVATE-SCRIPT">'], reviewStatus: "agent_adjudicated" },
      { path: "literal", value: "NotStated", reviewStatus: "agent_single_pass" },
    ] as unknown as NonNullable<PresentationSource["reviewWorkflow"]>["fields"];
    typed.reviewWorkflow!.summary = { ...typed.reviewWorkflow!.summary, fieldReviewState: "known", fieldCount: 4,
      agentApprovedFieldCount: 2, singlePassFieldCount: 1, humanReviewRequiredFieldCount: 1,
      humanReviewedFieldCount: 0, unmappedFieldCount: 0 };
    const wrapper = mount(SourceReviewPanel, { props: { source: typed } });
    const rows = wrapper.findAll(".review-field-list .review-field");
    expect(rows[0].findAll("small").map((item) => item.text())).toEqual([
      "Alternatywa: z Polczarów · stan alternatywy nieustalony", "Alternatywa: z Polenców · stan alternatywy nieustalony", "Alternatywa: z Polenaców · stan alternatywy nieustalony",
    ]);
    expect(rows[0].find("p").text()).toBe("z Polcarów");
    expect(rows[0].attributes("data-review-state")).toBe("human_review_required");
    expect(rows[1].find("strong").text()).toBe("zmarły · zawód");
    expect(rows[1].find("p").text()).toBe("Nie podano w akcie");
    expect(rows[1].attributes("data-review-state")).toBe("agent_consensus");
    expect(rows[2].find("p").text()).toBe("o go. dzinie siódmej wieczorem");
    expect(rows[2].attributes("data-review-state")).toBe("agent_adjudicated");
    expect(rows[2].find("img").exists()).toBe(false);
    expect(rows[3].find("p").text()).toBe("NotStated");
    expect(wrapper.get(".review-workflow-panel .source-technical-note").text())
      .toContain("oryginalny kod obserwacji braku: NotStated");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("2 pól rozstrzygniętych agentowo");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("1 pól z jednym odczytem agentowym");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("1 pól dla Ciebie");
    expect(wrapper.html()).not.toContain("PRIVATE-EXTRA");
  });

  it("renders current and legacy scalar path/value aliases with escaped content and zero", () => {
    const wrapper = withFields([
      { fieldPath: "record.number", value: "3", status: "agent_consensus" },
      { field: "groom.age", value: 0, status: "human_review_required" },
      { path: "record.number", rawValue: 3, reviewStatus: "agent_consensus" },
      { path: "note", rawValue: '<img src=x onerror="PRIVATE-SCRIPT">', reviewStatus: "agent_single_pass" },
      { fieldPath: "same", path: "same", value: "3", rawValue: 3, status: "agent_consensus" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    expect(rows[0].find("strong").text()).toBe("record · number");
    expect(rows[0].find("p").text()).toBe("3");
    expect(rows[1].find("p").text()).toBe("0");
    expect(rows[2].find("strong").text()).toBe("record · number");
    expect(rows[2].find("p").text()).toBe("3");
    expect(rows[2].attributes("data-review-state")).toBe("agent_consensus");
    expect(rows[3].find("p img").exists()).toBe(false);
    expect(rows[3].find("p").text()).toContain('<img src=x');
    expect(rows[4].find("p").text()).toBe("3");
  });

  it("keeps null or absent primary values unknown without context or blind-reading inference", () => {
    const wrapper = withFields([
      { path: "record.number", rawValue: null, contextValue: "CONTEXT-ONLY", blindAValue: 3, blindBValue: 3, sourceQuote: "QUOTE-ONLY", reviewStatus: "human_review_required" },
      { path: "record.number", contextValue: "CONTEXT-ONLY", reviewStatus: "agent_single_pass" },
      { fieldPath: "current", value: null, rawValue: "LEGACY-FALLBACK", reviewStatus: "agent_consensus" },
    ]);
    const rows = wrapper.findAll(".review-field-list .review-field");
    rows.forEach((row) => expect(row.find("p").text()).toBe("wartość nieustalona"));
    expect(rows[0].attributes("data-review-state")).toBe("human_review_required");
    expect(rows[2].attributes("data-review-state")).toBe("unknown");
    expect(wrapper.html()).not.toMatch(/CONTEXT-ONLY|QUOTE-ONLY|LEGACY-FALLBACK/);
  });

  it("marks conflicting or malformed aliases unknown without selecting a winner or serializing objects", () => {
    const wrapper = withFields([
      { fieldPath: "CURRENT-PATH", path: "LEGACY-PATH", value: "kept", status: "agent_consensus" },
      { path: "valid", value: "CURRENT-VALUE", rawValue: "LEGACY-VALUE", status: "human_reviewed" },
      { fieldPath: {}, path: "LEGACY-FALLBACK", value: "kept", reviewStatus: "agent_consensus" },
      { path: "valid", rawValue: { private: "PRIVATE-EXTRA" }, reviewStatus: "agent_consensus" },
      { path: "valid", rawValue: Number.POSITIVE_INFINITY, reviewStatus: "agent_consensus" },
      { path: "valid", rawValue: false, reviewStatus: "agent_consensus" },
      { fieldPath: "CURRENT", field: "OTHER-CURRENT", value: "kept", status: "agent_consensus" },
    ]);
    wrapper.findAll(".review-field-list .review-field").forEach((row) => {
      expect(row.attributes("data-review-state")).toBe("unknown");
      expect(row.find(".is-agent").exists()).toBe(false);
      expect(row.find(".is-good").exists()).toBe(false);
    });
    expect(wrapper.html()).not.toMatch(/CURRENT-PATH|LEGACY-PATH|CURRENT-VALUE|LEGACY-VALUE|LEGACY-FALLBACK|PRIVATE-EXTRA|Infinity|OTHER-CURRENT/);
  });

  it("keeps every malformed formal field position as an unknown harmless placeholder", () => {
    const typed = structuredClone(source);
    typed.readingPipelineRuns = [];
    const fields = [null, "SECRET-PRIMITIVE", false, 42, [],
      { fieldPath: {}, status: null }, { field: [], status: { approval: true }, value: { private: "PRIVATE-METADATA" } },
      { fieldPath: 9, status: "unrecognized_status" }];
    typed.reviewWorkflow!.fields = fields as unknown as NonNullable<PresentationSource["reviewWorkflow"]>["fields"];
    typed.reviewWorkflow!.status = "human_reviewed";
    typed.reviewWorkflow!.summary = { ...typed.reviewWorkflow!.summary, fieldReviewState: "unknown", fieldCount: fields.length,
      agentApprovedFieldCount: null, singlePassFieldCount: null, humanReviewRequiredFieldCount: null,
      humanReviewedFieldCount: null, unmappedFieldCount: fields.length, hasAgentApproval: false, needsHumanReview: false };
    const wrapper = mount(SourceReviewPanel, { props: { source: typed } });
    const rows = wrapper.findAll(".review-field-list .review-field");
    expect(rows).toHaveLength(fields.length);
    rows.forEach((row, index) => {
      expect(row.attributes("data-review-position")).toBe(String(index + 1));
      expect(row.attributes("data-review-state")).toBe("unknown");
      expect(row.text()).toContain("stan kontroli nieustalony");
      expect(row.find("p").text()).toBe("wartość nieustalona");
      expect(row.find(".is-agent").exists()).toBe(false);
      expect(row.find(".is-good").exists()).toBe(false);
    });
    expect(wrapper.get(".review-workflow-panel .review-rationale").text()).toContain("Stan kontroli pól nieustalony");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("8 pól o nieustalonym stanie");
    expect(wrapper.get(".review-workflow-stats").text()).not.toContain("0 pól dla Ciebie");
    expect(wrapper.html()).not.toMatch(/SECRET-PRIMITIVE|PRIVATE-METADATA|unrecognized_status/);
  });

  it("shows known counts and legacy reviewStatus without merging single-pass into approval", () => {
    const typed = structuredClone(source);
    typed.readingPipelineRuns = [];
    typed.reviewWorkflow!.summary = { ...typed.reviewWorkflow!.summary, fieldReviewState: "known", fieldCount: 107,
      agentApprovedFieldCount: 91, singlePassFieldCount: 11, humanReviewRequiredFieldCount: 5,
      humanReviewedFieldCount: 0, unmappedFieldCount: 0 };
    typed.reviewWorkflow!.fields = [{ field: "legacy-consensus", reviewStatus: "agent_consensus" },
      { field: "legacy-single", reviewStatus: "agent_single_pass" },
      { field: "conflict", status: "agent_consensus", reviewStatus: "human_reviewed" }] as unknown as NonNullable<PresentationSource["reviewWorkflow"]>["fields"];
    const wrapper = mount(SourceReviewPanel, { props: { source: typed } });
    expect(wrapper.get(".review-workflow-stats").text()).toContain("91 pól rozstrzygniętych agentowo");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("11 pól z jednym odczytem agentowym");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("5 pól dla Ciebie");
    expect(wrapper.get('[data-review-state="agent_consensus"]').text()).toContain("zgoda agentów");
    expect(wrapper.get('[data-review-state="agent_single_pass"]').text()).toContain("jeden odczyt agentowy");
    expect(wrapper.get('[data-review-state="unknown"]').text()).toContain("stan kontroli nieustalony");
  });

  it("renders partial known subset and unknown counts truthfully", async () => {
    const typed = structuredClone(source);
    typed.readingPipelineRuns = [];
    typed.reviewWorkflow!.summary = { ...typed.reviewWorkflow!.summary, fieldReviewState: "partial", fieldCount: 5,
      agentApprovedFieldCount: 2, singlePassFieldCount: 1, humanReviewRequiredFieldCount: 0,
      humanReviewedFieldCount: 0, unmappedFieldCount: 2 };
    const wrapper = mount(SourceReviewPanel, { props: { source: typed } });
    expect(wrapper.get(".review-workflow-panel .review-rationale").text()).toContain("znany podzbiór pól");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("2 pól o nieustalonym stanie");
    typed.reviewWorkflow!.summary = { ...typed.reviewWorkflow!.summary, fieldReviewState: "unknown",
      agentApprovedFieldCount: null, singlePassFieldCount: null, humanReviewRequiredFieldCount: null,
      humanReviewedFieldCount: null, unmappedFieldCount: 5 };
    await wrapper.setProps({ source: structuredClone(typed) });
    expect(wrapper.get(".review-workflow-panel .review-rationale").text()).toContain("Stan kontroli pól nieustalony");
    expect(wrapper.get(".review-workflow-stats").text()).toContain("nieustalone pól rozstrzygniętych agentowo");
    expect(wrapper.get(".review-workflow-stats").text()).not.toContain("0 pól dla Ciebie");
  });

  it("keeps comparisons, review workflow and coverage on the canonical source page", () => {
    const wrapper = mount(SourceReviewPanel, { props: { source } });

    expect(wrapper.get(".comparison-panel").text()).toContain("Odczyt literalny");
    expect(wrapper.get(".reading-pipeline-panel").text()).toContain("80% pól wymaganych");
    expect(wrapper.get(".reading-pipeline-panel").text()).toContain("2 niezależne odczyty");
    expect(wrapper.get(".reading-pipeline-panel").text()).toContain("3 rozbieżności");
    expect(wrapper.get(".reading-pipeline-panel").text()).toContain("adjudykator");
    expect(wrapper.get(".reading-verification-card").text()).toContain("Pola odczytane z obrazu");
    expect(wrapper.get(".reading-verification-card").text()).toContain("odczyty sporne");
    expect(wrapper.get(".comparison-panel").text()).toContain("Kubiaków pozostaje hipotezą kontekstową");
    expect(wrapper.get(".review-workflow-panel").text()).toContain("1 pól dla Ciebie");
    expect(wrapper.get(".source-coverage-panel").text()).toContain("Transkrypcja");
    expect(wrapper.get(".source-coverage-panel").text()).toContain("1/1");
  });

  it("does not pass a PDF artifact to the raster crop renderer", () => {
    const pdfSource = structuredClone(source);
    pdfSource.artifacts = [{
      id: "artifact",
      viewer: "pdf",
      mediaType: "application/pdf",
      label: "PDF",
      url: "/artifact.pdf",
    }];
    const wrapper = mount(SourceReviewPanel, { props: { source: pdfSource } });
    expect(wrapper.find(".reading-verification-image-loader").exists()).toBe(false);
    expect(wrapper.get(".reading-field-crop.is-missing").text()).toContain("Brak bezpiecznego wycinka");
  });

  it("resolves a pipeline artifact through an explicit artifact alias", () => {
    const aliasedSource = structuredClone(source);
    aliasedSource.artifacts = [{
      id: "legacy-artifact",
      aliasIds: ["artifact"],
      viewer: "image",
      mediaType: "image/jpeg",
      label: "Oryginał",
      url: "/local-artifacts/legacy-artifact",
    }];

    const wrapper = mount(SourceReviewPanel, { props: { source: aliasedSource } });

    expect(wrapper.find(".reading-verification-image-loader").exists()).toBe(true);
    expect(wrapper.get(".reading-verification-image-loader").attributes("src"))
      .toBe("/local-artifacts/legacy-artifact");
  });
});
