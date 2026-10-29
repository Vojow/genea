import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { PresentationSource } from "../types/presentation";
import SourceDocumentReader from "./SourceDocumentReader.vue";

const source: PresentationSource = {
  id: "source-test",
  title: "Akt próbny",
  artifacts: [{ id: "scan", viewer: "image", label: "scan.jpg", url: "/local-artifacts/scan" }],
  readings: [
    { id: "pl-v1", readingId: "pl", type: "human_review", version: 1, status: "human_reviewed", segments: [{ sequence: 1, language: "polski", text: "Starszy tekst polski" }] },
    { id: "pl-v2", readingId: "pl", type: "human_review", version: 2, status: "human_reviewed", segments: [{ sequence: 1, language: "polski", text: "Aktualny tekst polski" }, { sequence: 2, language: "rosyjski", text: "Текст источника" }] },
    { id: "la-v1", readingId: "la", type: "blind", version: 1, status: "unreviewed", segments: [{ sequence: 1, language: "łacina", text: "Textus latinus" }] },
  ],
};

describe("source document reader", () => {
  it("shows the original and one primary-language reading, with alternatives collapsed", async () => {
    const wrapper = mount(SourceDocumentReader, { props: { source, preferredLanguage: "pl" } });

    expect(wrapper.find(".original-pane img").attributes("src")).toBe("/local-artifacts/scan");
    expect(wrapper.find(".transcript-pane .transcript").text()).toContain("Aktualny tekst polski");
    expect(wrapper.find(".transcript-pane .transcript").text()).not.toContain("Starszy tekst polski");
    expect(wrapper.find(".transcript-pane .transcript").text()).not.toContain("Textus latinus");
    expect(wrapper.find(".transcript-pane .transcript").text()).not.toContain("Текст источника");
    expect(wrapper.find<HTMLDetailsElement>(".reading-language-supplement").element.open).toBe(false);
    expect(wrapper.find(".reading-language-supplement").text()).toContain("Текст источника");
    expect(wrapper.find<HTMLDetailsElement>(".reading-version-picker").element.open).toBe(false);

    const latinOption = wrapper.findAll(".reading-version-option").find((item) => item.text().includes("łacina"));
    await latinOption?.trigger("click");
    expect(wrapper.find(".transcript-pane .transcript").text()).toContain("Textus latinus");
    expect(wrapper.find(".transcript-pane .transcript").text()).not.toContain("Aktualny tekst polski");

    await wrapper.get(".return-primary-button").trigger("click");
    expect(wrapper.find(".transcript-pane .transcript").text()).toContain("Aktualny tekst polski");

    await wrapper.get<HTMLInputElement>('[aria-label="Pokaż oryginał dokumentu"]').setValue(false);
    expect(wrapper.find(".original-pane").exists()).toBe(false);
  });
});
