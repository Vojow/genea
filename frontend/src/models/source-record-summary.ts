import type { PresentationSource, SourceRecordSummary } from "../types/presentation";
import { sourceEventGroup, type SourceEventRecord } from "./source-event-projector";

function text(value: unknown): string {
  return value == null ? "" : String(value).trim();
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function unique(values: unknown[]): string[] {
  return [...new Set(values.map(text).filter(Boolean))];
}

function sourceTypeLabel(sourceType: string, eventGroups: string[]): string {
  if (sourceType === "act") {
    if (eventGroups.length === 1 && eventGroups[0] === "marriage") return "akt małżeństwa";
    if (eventGroups.length === 1 && eventGroups[0] === "birth-baptism") return "akt urodzenia i chrztu";
    if (eventGroups.length === 1 && eventGroups[0] === "death-burial") return "akt zgonu i pogrzebu";
    return "akt metrykalny";
  }
  const labels: Record<string, string> = {
    register: "księga",
    register_page: "strona księgi",
    index: "indeks",
    document: "dokument",
    image: "obraz źródła",
  };
  return labels[sourceType] || sourceType || "materiał źródłowy";
}

function repositoryFrom(source: PresentationSource): string {
  const metadata = source.metadata || {};
  const declared = text(metadata.repository || metadata.legacy_service);
  if (declared) return declared;
  if (!source.url) return "";
  try {
    return new URL(source.url).hostname;
  } catch {
    return "";
  }
}

export function projectSourceRecordSummary(
  source: PresentationSource,
  events: SourceEventRecord[],
): SourceRecordSummary {
  const exported = source.recordSummary;
  const eventTypes = exported?.eventTypes?.length
    ? exported.eventTypes
    : unique(events.flatMap((event) => list(event.originalTypes).length ? list(event.originalTypes) : [event.type]));
  const eventGroups = exported?.eventGroups?.length
    ? exported.eventGroups
    : unique(eventTypes.map(sourceEventGroup));
  const dates = unique(events.map((event) => event.dateFrom || event.date));
  const date = text(exported?.date || source.date || (dates.length === 1 ? dates[0] : "")) || null;
  const dateBasis = exported?.dateBasis || (source.date ? "source_item.date_text" : date ? "linked_event.date_from" : null);

  const metadata = source.metadata || {};
  let recordNumber = text(exported?.recordNumber || metadata.actNumber || metadata.act_number || metadata.recordNumber || metadata.record_number);
  let recordNumberBasis = exported?.recordNumberBasis || (recordNumber ? "source_item.metadata" : null);
  if (!recordNumber) {
    const match = source.title.match(/(?<!\d)(\d{1,5}\s*\/\s*\d{4})(?!\d)/);
    recordNumber = match ? match[1].replace(/\s+/g, "") : "";
    if (recordNumber) recordNumberBasis = "source_item.title";
  }

  const places = exported?.places?.length
    ? exported.places
    : unique(events.flatMap((event) => list(event.originalPlaces).length ? list(event.originalPlaces) : [event.place]));
  const citationLocators = exported?.citationLocators?.length
    ? exported.citationLocators
    : unique((source.citations || []).map((citation) => citation.locator));
  const participantIds = unique(events.flatMap((event) => list(event.participants).map((participant) => {
    return participant && typeof participant === "object" ? (participant as Record<string, unknown>).occurrenceId : "";
  })));
  const missingCanonicalFields = exported?.missingCanonicalFields || [
    ...(!source.date ? ["source_date"] : []),
    ...(!source.locator ? ["source_locator"] : []),
  ];

  return {
    sourceType: exported?.sourceType || source.type,
    typeLabel: sourceTypeLabel(text(exported?.sourceType || source.type), eventGroups),
    eventGroups,
    eventTypes,
    date,
    dateBasis,
    recordNumber: recordNumber || null,
    recordNumberBasis,
    places,
    repository: exported?.repository || repositoryFrom(source) || null,
    canonicalLocator: exported?.canonicalLocator || source.locator || null,
    citationLocators,
    eventCount: exported?.eventCount ?? events.length,
    participantCount: exported?.participantCount ?? participantIds.length,
    missingCanonicalFields,
  };
}
