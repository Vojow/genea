import type { ReviewScale } from "./api";

type FieldReadings = Partial<Record<ReviewScale, string>>;
type StoredReadings = Record<string, FieldReadings>;

function storageKey(runId: string): string {
  return `genea:blind-review:${runId}`;
}

function parseStored(value: string | null): StoredReadings {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const safe: StoredReadings = {};
    for (const [fieldId, fieldValue] of Object.entries(parsed)) {
      if (typeof fieldValue !== "object" || fieldValue === null || Array.isArray(fieldValue)) continue;
      const readings: FieldReadings = {};
      for (const scale of ["field", "context", "fullAct"] as const) {
        const text = (fieldValue as Record<string, unknown>)[scale];
        if (typeof text === "string") readings[scale] = text;
      }
      safe[fieldId] = readings;
    }
    return safe;
  } catch {
    return {};
  }
}

export function loadLocalReadings(runId: string): StoredReadings {
  return parseStored(sessionStorage.getItem(storageKey(runId)));
}

export function saveLocalReading(runId: string, fieldId: string, scale: ReviewScale, value: string): void {
  const stored = loadLocalReadings(runId);
  stored[fieldId] = { ...stored[fieldId], [scale]: value };
  sessionStorage.setItem(storageKey(runId), JSON.stringify(stored));
}
