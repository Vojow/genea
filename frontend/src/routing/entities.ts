import type { EntityKind } from "../router";
import type { PresentationPayload, ResearchBacklog, ResearchBacklogItem, VolumeRunSummary } from "../types/presentation";
import { PersonContextIndex } from "./person-context";


export type EntityAvailability = "found" | "private" | "not_exported" | "missing";

export interface EntityReference {
  kind: EntityKind;
  id: string;
  label: string;
  status?: string | null;
  availability: EntityAvailability;
  raw?: Record<string, unknown>;
}

export interface SearchEntry extends EntityReference {
  detail: string;
  searchText: string;
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function objectId(value: unknown): string {
  return stringValue(objectRecord(value)?.id);
}

function labelFor(kind: EntityKind, item: Record<string, unknown>): string {
  if (kind === "run") {
    const runLabel = [stringValue(item.backend), stringValue(item.model)].filter(Boolean).join(" ");
    if (runLabel) return runLabel;
  }
  if (kind === "household" && item.houseNumber) return `Dom ${stringValue(item.houseNumber)}`;
  const candidates = kind === "question"
    ? [item.question, item.label, item.title]
    : kind === "person"
      ? [item.name, item.preferredName, item.label, item.title]
      : kind === "event"
        ? [item.label, item.title, item.type]
        : [item.label, item.title, item.name, item.question];
  return candidates.map(stringValue).find(Boolean) || "Obiekt bez etykiety";
}

function statusFor(item: Record<string, unknown>): string | null {
  return stringValue(item.status || item.workflowStage || item.classification) || null;
}

function availabilityFor(item: Record<string, unknown>): EntityAvailability {
  const value = stringValue(item.availability);
  return (["found", "private", "not_exported", "missing"] as const).includes(value as EntityAvailability)
    ? value as EntityAvailability
    : "found";
}

function uniqueRecords(values: unknown[]): Record<string, unknown>[] {
  const seen = new Set<string>();
  const result: Record<string, unknown>[] = [];
  for (const value of values) {
    const item = objectRecord(value);
    const id = objectId(item);
    if (!item || !id || seen.has(id)) continue;
    seen.add(id);
    result.push(item);
  }
  return result;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? uniqueRecords(value) : [];
}

function caseRecords(backlog: ResearchBacklog | undefined): Record<string, unknown>[] {
  const items = backlog?.items || [];
  // An explicit catalogue is authoritative, including an empty catalogue.
  // Question references must never recreate cases omitted by the exporter.
  if (backlog?.cases !== undefined) {
    return records(backlog.cases).map((item) => ({
      ...item,
      questions: items.filter((question) => question.caseId === item.id),
    }));
  }
  const byId = new Map<string, Record<string, unknown>>();
  for (const item of items) {
    if (!item.caseId) continue;
    const previous = byId.get(item.caseId);
    if (previous) {
      (previous.questions as ResearchBacklogItem[]).push(item);
      continue;
    }
    byId.set(item.caseId, {
      id: item.caseId,
      title: item.caseTitle || "Sprawa badawcza",
      status: "active",
      summary: null,
      nextActions: [],
      questions: [item],
    });
  }
  return [...byId.values()];
}

function lineRecords(payload: PresentationPayload): Record<string, unknown>[] {
  const scopes = objectRecord(payload.graphScopes)?.scopes;
  const branches = objectRecord(payload.familyTree)?.branches;
  return uniqueRecords([...(Array.isArray(scopes) ? scopes : []), ...(Array.isArray(branches) ? branches : [])]);
}

function familyHouseholdRecords(payload: PresentationPayload): Record<string, unknown>[] {
  const scopes = objectRecord(payload.graphScopes)?.scopes;
  if (!Array.isArray(scopes)) return [];
  return uniqueRecords(scopes.flatMap((scope) => {
    const item = objectRecord(scope);
    return item ? records(item.familyHouseholds) : [];
  }));
}

function householdRecords(payload: PresentationPayload): Record<string, unknown>[] {
  return uniqueRecords([...records(payload.households), ...familyHouseholdRecords(payload)]);
}

function runRecords(payload: PresentationPayload): Record<string, unknown>[] {
  const result: Record<string, unknown>[] = [];
  for (const volume of payload.volumeIndexes || []) {
    for (const run of volume.runs || []) {
      result.push({ ...run, volumeId: volume.id, volumeLabel: volume.label });
    }
  }
  return uniqueRecords(result);
}

function clusterRecords(payload: PresentationPayload): Record<string, unknown>[] {
  const explicit = records(payload.clusters);
  const households = householdRecords(payload).map((item) => ({
    id: `cluster:household:${objectId(item)}`,
    label: `Gospodarstwo · ${labelFor("household", item)}`,
    status: "source_context",
    basis: "household",
    householdId: objectId(item),
    backingNodeIds: item.occurrenceIds || item.personIds || [],
  }));
  return uniqueRecords([...explicit, ...households]);
}

export class EntityIndex {
  readonly byKind = new Map<EntityKind, Map<string, EntityReference>>();
  readonly searchEntries: SearchEntry[] = [];
  readonly personContexts: PersonContextIndex;

  constructor(readonly payload: PresentationPayload) {
    this.personContexts = new PersonContextIndex(payload);
    const familyPeople = records(objectRecord(payload.familyTree)?.people);
    const sources = payload.sources as unknown as Record<string, unknown>[];
    const questions = (payload.researchBacklog?.items || []) as unknown as Record<string, unknown>[];
    const volumes = (payload.volumeIndexes || []) as unknown as Record<string, unknown>[];
    const collections: Record<EntityKind, Record<string, unknown>[]> = {
      person: this.personContexts.contexts.length
        ? this.personContexts.contexts.map((context) => this.personContexts.profileRecord(context))
        : uniqueRecords([...records(payload.people), ...familyPeople]),
      line: lineRecords(payload),
      // The privacy-filtered canonical catalogue is the only routed place
      // registry. map.places is a geospatial subset with the same backing IDs,
      // never a fallback catalogue of presentation-only identities.
      place: records(payload.places),
      event: records(payload.events),
      source: uniqueRecords(sources),
      household: householdRecords(payload),
      cluster: clusterRecords(payload),
      case: caseRecords(payload.researchBacklog),
      question: uniqueRecords(questions),
      volume: uniqueRecords(volumes),
      run: runRecords(payload),
    };
    for (const [kind, items] of Object.entries(collections) as Array<[EntityKind, Record<string, unknown>[]]>) {
      const map = new Map<string, EntityReference>();
      for (const item of items) {
        const id = objectId(item);
        if (!id) continue;
        const availability = availabilityFor(item);
        const reference: EntityReference = {
          kind,
          id,
          label: labelFor(kind, item),
          status: statusFor(item),
          availability,
          raw: item,
        };
        map.set(id, reference);
        if (availability === "found") {
          const identityScope = stringValue(item.identityScope);
          const detail = [
            identityScope === "hypothesis_group" ? "grupa hipotez · nierozstrzygnięta" : identityScope === "presentation_only" ? "profil prezentacyjny" : statusFor(item),
            kind === "person" ? stringValue(item.lifeSpanLabel) : "",
            stringValue(item.date || item.scope || item.description),
          ].filter(Boolean).join(" · ");
          const literalNames = kind === "person"
            ? records(payload.occurrences)
              .filter((occurrence) => (item.occurrenceIds as unknown[] || []).includes(occurrence.id))
              .flatMap((occurrence) => [stringValue(occurrence.name), stringValue(occurrence.rawName)])
            : [];
          const aliases = Array.isArray(item.aliases) ? item.aliases.map(stringValue) : [];
          this.searchEntries.push({
            ...reference,
            detail,
            searchText: `${reference.label} ${detail} ${id} ${aliases.join(" ")} ${literalNames.join(" ")}`.toLocaleLowerCase("pl-PL"),
          });
        }
      }
      this.byKind.set(kind, map);
    }
  }

  resolve(kind: EntityKind, id: string): EntityReference {
    if (kind === "person" && this.personContexts.contexts.length) {
      const context = this.personContexts.resolveAny(id);
      if (context) {
        return this.byKind.get("person")?.get(context.publicProfileId) || {
          kind: "person",
          id: context.publicProfileId,
          label: context.label,
          status: context.status,
          availability: "found",
          raw: this.personContexts.profileRecord(context),
        };
      }
    }
    return this.byKind.get(kind)?.get(id) || {
      kind,
      id,
      label: "Obiekt niedostępny",
      availability: "missing",
    };
  }

  resolvePersonRoute(id: string): EntityReference {
    if (!this.personContexts.contexts.length) return this.resolve("person", id);
    const context = this.personContexts.resolveRoute(id);
    return context ? this.resolve("person", context.personKey) : {
      kind: "person", id, label: "Obiekt niedostępny", availability: "missing",
    };
  }

  all(kind: EntityKind): EntityReference[] {
    return [...(this.byKind.get(kind)?.values() || [])];
  }

  findRun(id: string): VolumeRunSummary | null {
    return (this.resolve("run", id).raw || null) as VolumeRunSummary | null;
  }

  search(query: string, limit = 20): SearchEntry[] {
    const normalized = query.trim().toLocaleLowerCase("pl-PL");
    if (!normalized) return [];
    return this.searchEntries.filter((entry) => entry.searchText.includes(normalized)).slice(0, limit);
  }
}

export interface ReferenceIntegrityIssue {
  ownerKind: EntityKind;
  ownerId: string;
  field: string;
  targetKind: EntityKind;
  targetId: string;
  availability: EntityAvailability;
}

const REFERENCE_FIELDS: Record<string, EntityKind> = {
  sourceId: "source",
  sourceIds: "source",
  personId: "person",
  personIds: "person",
  placeId: "place",
  placeIds: "place",
  eventId: "event",
  eventIds: "event",
  householdId: "household",
  householdIds: "household",
  caseId: "case",
  caseIds: "case",
  questionId: "question",
  questionIds: "question",
  volumeId: "volume",
  volumeIds: "volume",
};

/** Report broken typed references without treating them as identity evidence. */
export function auditEntityReferences(index: EntityIndex): ReferenceIntegrityIssue[] {
  const issues: ReferenceIntegrityIssue[] = [];
  for (const [ownerKind, entities] of index.byKind) {
    for (const owner of entities.values()) {
      if (!owner.raw) continue;
      for (const [field, targetKind] of Object.entries(REFERENCE_FIELDS)) {
        const rawValue = owner.raw[field];
        const values = Array.isArray(rawValue) ? rawValue : [rawValue];
        for (const value of values) {
          const targetId = stringValue(value);
          if (!targetId) continue;
          const availability = index.resolve(targetKind, targetId).availability;
          if (availability === "found" || availability === "private" || availability === "not_exported") continue;
          issues.push({ ownerKind, ownerId: owner.id, field, targetKind, targetId, availability });
        }
      }
    }
  }
  return issues;
}

export function labelForKind(kind: EntityKind): string {
  return ({
    person: "Osoba",
    line: "Linia rodzinna",
    place: "Miejsce",
    event: "Zdarzenie",
    source: "Akt lub materiał",
    household: "Gospodarstwo",
    cluster: "Klaster",
    case: "Sprawa badawcza",
    question: "Pytanie badawcze",
    volume: "Wolumin",
    run: "Przebieg OCR / HTR / AI",
  } satisfies Record<EntityKind, string>)[kind];
}
