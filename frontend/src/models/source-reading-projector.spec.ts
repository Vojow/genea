import { describe, expect, it } from "vitest";

import type { SourceReading } from "../types/presentation";
import { projectSourceReadings, readingTypeLabel } from "./source-reading-projector";

function reading(overrides: Partial<SourceReading> & Pick<SourceReading, "id" | "type">): SourceReading {
  return {
    version: 1,
    segments: [],
    ...overrides,
  };
}

describe("source reading projector", () => {
  it("presents the newest reviewed reading in the interface language and keeps alternatives supplemental", () => {
    const readings: SourceReading[] = [
      reading({ id: "polish-v1", readingId: "polish", type: "human_review", version: 1, status: "human_reviewed", segments: [{ sequence: 1, language: "polski", text: "Starszy odczyt" }] }),
      reading({ id: "polish-v2", readingId: "polish", type: "human_review", version: 2, status: "human_reviewed", segments: [{ sequence: 1, language: "polski", text: "Aktualny odczyt" }] }),
      reading({ id: "latin-v1", readingId: "latin", type: "blind", version: 1, status: "unreviewed", segments: [{ sequence: 1, language: "łacina", text: "Lectio latina" }] }),
    ];

    const model = projectSourceReadings(readings, "pl-PL");

    expect(model.primary?.id).toBe("polish-v2");
    expect(model.preferredLanguageMatched).toBe(true);
    expect(model.currentReadings.map((item) => item.id)).toEqual(["polish-v2", "latin-v1"]);
    expect(model.supplemental).toEqual([
      expect.objectContaining({ reading: expect.objectContaining({ id: "latin-v1" }), olderVersion: false }),
      expect.objectContaining({ reading: expect.objectContaining({ id: "polish-v1" }), olderVersion: true }),
    ]);
  });

  it("marks a fallback when no reading matches the interface language", () => {
    const model = projectSourceReadings([
      reading({ id: "latin", type: "contextual", segments: [{ sequence: 1, language: "lat", text: "Lectio" }] }),
    ], "pl");

    expect(model.primary?.id).toBe("latin");
    expect(model.preferredLanguageMatched).toBe(false);
  });

  it("shows the Polish pipeline proposal as the readable default without calling it reviewed", () => {
    const model = projectSourceReadings([
      reading({ id: "literal", type: "pipeline_transcription", status: "needs_review", segments: [{ sequence: 1, language: "rus", text: "Текст" }] }),
      reading({ id: "translation", type: "pipeline_translation", status: "needs_review", segments: [{ sequence: 1, language: "pl", text: "Odczyt po polsku" }] }),
      reading({ id: "extract", type: "pipeline_extract", status: "needs_review", segments: [{ sequence: 1, language: "pl", text: "Ekstrakt" }] }),
    ], "pl");

    expect(model.primary?.id).toBe("translation");
    expect(readingTypeLabel(model.primary?.type)).toBe("propozycja głównego odczytu po polsku");
    expect(model.primary?.status).toBe("needs_review");
  });
});
