import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { SourceReadingVerificationCard } from "../types/presentation";
import ReadingVerificationCard from "./ReadingVerificationCard.vue";

const card: SourceReadingVerificationCard = {
  schema: "genea.reading-verification-card/v1",
  summary: {
    fieldCount: 3,
    agreedCount: 1,
    disputedCount: 1,
    incompleteCount: 1,
    notComparedCount: 0,
    humanReviewRequiredCount: 2,
  },
  fields: [
    {
      path: "event.date",
      role: "event",
      criticality: "critical",
      proposal: { value: "14/26 IV 1896", normalizedValue: "1896-04-26", confidence: 0.97, certainty: "Certain", alternatives: [], materialAlternatives: [], sourceQuote: "четырнадцатого ... Апреля" },
      blindReadings: [
        { readingId: "blind-a", readerId: "reader-a", value: "14/26 IV 1896" },
        { readingId: "blind-b", readerId: "reader-b", value: "14/26 IV 1896" },
      ],
      comparison: { status: "agreed", differenceKind: "agreement", values: [] },
      workflow: { status: "human_review_required", basis: "pipeline_assessment" },
      issues: [{ id: "issue-grounding", fieldPath: "event.date", reasonCode: "weakly_grounded_agreement", priority: 2, recommendedRole: "human_curator" }],
      backing: { sourceItemId: "source-m8", artifactId: "artifact-m8", sourceSegmentId: "segment-date", bbox: { x: 20, y: 30, width: 300, height: 90 } },
    },
    {
      path: "bride.name",
      role: "bride",
      criticality: "critical",
      proposal: { value: "Зр[е/о]мбковская", normalizedValue: "Zrąbkowska", confidence: 0.6, certainty: "Ambiguous", alternatives: ["Zrębkowska"], materialAlternatives: ["Źrąbkowska"], sourceQuote: "Катаржиною ..." },
      blindReadings: [
        { readingId: "blind-a", readerId: "reader-a", value: "Зрембковская" },
        { readingId: "blind-b", readerId: "reader-b", value: "Зромбковская" },
      ],
      comparison: { status: "disputed", differenceKind: "different_value", values: [] },
      workflow: { status: "human_review_required", basis: "pipeline_assessment" },
      issues: [],
      backing: { sourceItemId: "source-m8", artifactId: "artifact-m8", bbox: { x: 40, y: 130, width: 320, height: 95 } },
    },
    {
      path: "witnesses",
      role: "witness",
      criticality: "standard",
      proposal: { value: "Jakub Kruszyński", normalizedValue: null, confidence: 0.84, certainty: "Probable", alternatives: [], materialAlternatives: [], sourceQuote: "Якова Крушинскаго" },
      blindReadings: [{ readingId: "blind-a", readerId: "reader-a", value: "Jakub Kruszyński" }],
      comparison: { status: "incomplete", differenceKind: "missing_in_some", values: [] },
      workflow: { status: "machine_proposal", basis: "pipeline_assessment" },
      issues: [],
      backing: { sourceItemId: "source-m8", artifactId: "artifact-m8", bbox: null },
    },
  ],
  issues: [],
};

describe("ReadingVerificationCard", () => {
  it("shows every proposed field and keeps agreement separate from human review", () => {
    const wrapper = mount(ReadingVerificationCard, { props: { card } });

    expect(wrapper.findAll(".reading-verification-field")).toHaveLength(3);
    const date = wrapper.find('[data-comparison="agreed"]');
    expect(date.text()).toContain("odczyty zgodne");
    expect(date.text()).toContain("wymaga Twojej kontroli");
    expect(date.text()).not.toContain("sprawdzone przez Ciebie");
    expect(date.text()).toContain("Odczyt 1");
    expect(date.text()).toContain("Odczyt 2");
    expect(date.get(".reading-field-value").text()).toBe("14/26 IV 1896");
    expect(date.text()).toContain("Interpretacja / forma polska: 1896-04-26");
  });

  it("renders an incomplete issue safely when no recommended role exists", async () => {
    const incomplete = structuredClone(card);
    incomplete.fields[0].issues = [{ reasonCode: "weakly_grounded_agreement" }];
    const wrapper = mount(ReadingVerificationCard, { props: { card: incomplete } });
    expect(wrapper.text()).toContain("kontrola kuratorska");
  });

  it("filters disputed and incomplete fields without hiding their provenance", async () => {
    const wrapper = mount(ReadingVerificationCard, { props: { card } });
    await wrapper.get(".reading-verification-filters button:nth-child(3)").trigger("click");

    expect(wrapper.findAll(".reading-verification-field")).toHaveLength(2);
    expect(wrapper.text()).toContain("Panna młoda · imię i nazwisko");
    expect(wrapper.text()).toContain("Świadkowie");
    expect(wrapper.text()).not.toContain("Data zdarzenia");
  });

  it("offers a presentation mode while keeping disputes visible", async () => {
    const wrapper = mount(ReadingVerificationCard, { props: { card } });
    await wrapper.get(".reading-presentation-toggle").trigger("click");

    expect(wrapper.classes()).toContain("is-presentation");
    expect(wrapper.text()).toContain("odczyty sporne");
    expect(wrapper.findAll(".reading-field-provenance")).toHaveLength(0);
  });
});
