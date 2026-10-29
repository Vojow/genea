import type { SourceReading } from "../types/presentation";

export interface ProjectedSourceReading {
  reading: SourceReading;
  olderVersion: boolean;
}

export interface SourceReaderModel {
  primary: SourceReading | null;
  currentReadings: SourceReading[];
  supplemental: ProjectedSourceReading[];
  preferredLanguage: string;
  preferredLanguageLabel: string;
  preferredLanguageMatched: boolean;
}

const LANGUAGE_ALIASES: Record<string, string[]> = {
  pl: ["pl", "pol", "polski", "polish"],
  la: ["la", "lat", "lacina", "łacina", "latin"],
  ru: ["ru", "rus", "rosyjski", "russian"],
  de: ["de", "deu", "ger", "niemiecki", "german"],
};

const LANGUAGE_LABELS: Record<string, string> = {
  pl: "polsku",
  la: "łacinie",
  ru: "rosyjsku",
  de: "niemiecku",
};

const TYPE_LABELS: Record<string, string> = {
  human_review: "odczyt sprawdzony przez człowieka",
  legacy_curated_transcription: "transkrypcja kuratorowana",
  agent_review: "odczyt po przeglądzie agentowym",
  contextual: "odczyt kontekstowy",
  blind: "odczyt niezależny",
  legacy_ai_import: "odczyt zaimportowany z AI",
  transcription: "transkrypcja",
  translation: "tłumaczenie",
  transliteration: "transliteracja",
  pipeline_transcription: "propozycja literalnej transkrypcji AI",
  pipeline_transliteration: "propozycja transliteracji AI",
  pipeline_translation: "propozycja głównego odczytu po polsku",
  pipeline_extract: "propozycja ekstraktu genealogicznego",
};

const STATUS_LABELS: Record<string, string> = {
  "human-reviewed": "sprawdzony przez człowieka",
  reviewed: "sprawdzony",
  approved: "zatwierdzony",
  final: "wersja końcowa",
  "agent-adjudicated": "rozstrzygnięty agentowo",
  "agent-consensus": "zgoda agentów",
  needs_review: "wymaga sprawdzenia",
  "needs-review": "wymaga sprawdzenia",
  unreviewed: "niesprawdzony",
  "imported-unverified": "importowany, niezweryfikowany",
};

function normalized(value?: string | null): string {
  return (value || "")
    .trim()
    .toLocaleLowerCase("pl-PL")
    .replaceAll("_", "-");
}

export function languageFamily(value?: string | null): string {
  const candidate = normalized(value);
  if (!candidate) return "unknown";
  for (const [family, aliases] of Object.entries(LANGUAGE_ALIASES)) {
    if (aliases.some((alias) => candidate === alias || candidate.startsWith(`${alias}-`) || candidate.includes(alias))) {
      return family;
    }
  }
  return candidate;
}

export function readingLanguages(reading: SourceReading): string[] {
  return [...new Set(reading.segments.map((segment) => languageFamily(segment.language)).filter((item) => item !== "unknown"))];
}

export function languageLabel(value?: string | null): string {
  const family = languageFamily(value);
  return LANGUAGE_LABELS[family] || value || "wybranym języku";
}

export function readingTypeLabel(value?: string | null): string {
  return TYPE_LABELS[value || ""] || (value || "odczyt").replaceAll("_", " ");
}

export function readingStatusLabel(reading: SourceReading): string {
  const status = normalized(reading.readingStatus || reading.status);
  return STATUS_LABELS[status] || status.replaceAll("-", " ") || "bez statusu kontroli";
}

function numericVersion(value: number | string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function compareVersions(first: SourceReading, second: SourceReading): number {
  return numericVersion(second.version) - numericVersion(first.version)
    || (second.createdAt || "").localeCompare(first.createdAt || "")
    || second.id.localeCompare(first.id);
}

function streamId(reading: SourceReading): string {
  return reading.readingId || reading.id;
}

function qualityScore(reading: SourceReading, preferredLanguage: string): number {
  const status = normalized(reading.readingStatus || reading.status);
  const type = normalized(reading.type);
  const languageMatch = readingLanguages(reading).includes(preferredLanguage);
  let score = languageMatch ? 10_000 : 0;

  if (["human-reviewed", "reviewed", "approved", "final"].includes(status)) score += 900;
  else if (["agent-adjudicated", "agent-consensus"].includes(status)) score += 700;
  else if (["needs-review", "unreviewed"].includes(status)) score += 100;

  if (type === "human-review") score += 850;
  else if (type === "legacy-curated-transcription") score += 760;
  else if (type === "agent-review") score += 620;
  else if (type === "contextual") score += 520;
  else if (type === "translation") score += 500;
  else if (type === "pipeline-translation") score += 480;
  else if (type === "pipeline-extract") score += 450;
  else if (type === "transliteration") score += 420;
  else if (type === "pipeline-transliteration") score += 400;
  else if (type === "blind") score += 300;
  else if (type === "pipeline-transcription") score += 250;
  else if (type === "legacy-ai-import") score += 100;

  score += reading.segments.some((segment) => segment.text.trim()) ? 2_000 : -2_000;
  return score;
}

export function projectSourceReadings(readings: SourceReading[], preferredLanguage = "pl"): SourceReaderModel {
  const preferredFamily = languageFamily(preferredLanguage) === "unknown" ? "pl" : languageFamily(preferredLanguage);
  const groups = new Map<string, SourceReading[]>();
  for (const reading of readings) {
    const group = groups.get(streamId(reading)) || [];
    group.push(reading);
    groups.set(streamId(reading), group);
  }

  const currentReadings = [...groups.values()]
    .map((versions) => [...versions].sort(compareVersions)[0])
    .sort((first, second) => qualityScore(second, preferredFamily) - qualityScore(first, preferredFamily) || first.id.localeCompare(second.id));
  const primary = currentReadings[0] || null;
  const currentIds = new Set(currentReadings.map((reading) => reading.id));
  const supplemental = readings
    .filter((reading) => reading.id !== primary?.id)
    .map((reading) => ({ reading, olderVersion: !currentIds.has(reading.id) }))
    .sort((first, second) => {
      if (first.olderVersion !== second.olderVersion) return first.olderVersion ? 1 : -1;
      return qualityScore(second.reading, preferredFamily) - qualityScore(first.reading, preferredFamily)
        || compareVersions(first.reading, second.reading);
    });

  return {
    primary,
    currentReadings,
    supplemental,
    preferredLanguage: preferredFamily,
    preferredLanguageLabel: languageLabel(preferredFamily),
    preferredLanguageMatched: Boolean(primary && readingLanguages(primary).includes(preferredFamily)),
  };
}
