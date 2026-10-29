export type SourceEventRecord = Record<string, unknown>;

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

function record(value: unknown): SourceEventRecord {
  return value && typeof value === "object" ? value as SourceEventRecord : {};
}

export function sourceEventGroup(value: string): string {
  const normalized = value.trim().toLocaleLowerCase("pl-PL");
  if (["marriage", "małżeństwo", "małżeństwo religijne"].includes(normalized)) return "marriage";
  if (["birth", "birth-baptism", "urodzenie", "urodzenie dziecka", "urodzenie i chrzest", "chrzest"].includes(normalized)) return "birth-baptism";
  if (["death", "death-burial", "zgon", "pogrzeb"].includes(normalized)) return "death-burial";
  return normalized || "unknown";
}

function eventQuality(event: SourceEventRecord): number {
  const genericRoles = new Set(["related-person", "witness-or-neighbor", "uczestnik", "participant"]);
  return list(event.participants).map(record).reduce((score, participant) => {
    const role = text(participant.role).toLocaleLowerCase("pl-PL");
    const specific = role && !genericRoles.has(role) ? 4 : 0;
    const described = [participant.age, participant.occupation, participant.residence, participant.statusText].filter(Boolean).length;
    return score + specific + described;
  }, 0);
}

function participantIdentityQuality(
  event: SourceEventRecord,
  occurrencesById?: ReadonlyMap<string, SourceEventRecord>,
): number {
  if (!occurrencesById) return 0;
  return list(event.participants).map(record).reduce((score, participant) => {
    const occurrence = occurrencesById.get(text(participant.occurrenceId));
    if (!occurrence) return score;
    return score + (text(occurrence.personId) ? 4 : 0) + (occurrence.confidence != null ? 1 : 0);
  }, 0);
}

function backingValues(event: SourceEventRecord, field: "mergedEventIds" | "originalTypes" | "originalPlaces", fallback: unknown): string[] {
  const values = list(event[field]).map(text).filter(Boolean);
  return values.length ? values : [text(fallback)].filter(Boolean);
}

function decorate(event: SourceEventRecord): SourceEventRecord {
  return {
    ...event,
    backingEventIds: backingValues(event, "mergedEventIds", event.id),
    originalTypes: backingValues(event, "originalTypes", event.type),
    originalPlaces: backingValues(event, "originalPlaces", event.place),
  };
}

function presentationBucketKey(event: SourceEventRecord): string {
  const groupingStatus = text(event.groupingStatus);
  const groupingCandidateId = text(event.groupingCandidateId);
  if (groupingStatus === "resolved_distinct") return `event:${text(event.id)}`;
  if (groupingCandidateId && ["resolved_same_record", "proposed"].includes(groupingStatus)) {
    return `candidate:${groupingCandidateId}`;
  }
  if (groupingStatus === "presentation_only_unregistered" && text(event.groupingProposalKey)) {
    return `proposal:${text(event.groupingProposalKey)}`;
  }
  if (groupingStatus === "resolved_same_record" && list(event.mergedEventIds).length > 1) {
    return `resolved:${text(event.recordKey || event.id)}`;
  }
  return `event:${text(event.id)}`;
}

export function projectSourceEvents(
  events: SourceEventRecord[],
  sourceType?: string | null,
  occurrencesById?: ReadonlyMap<string, SourceEventRecord>,
): SourceEventRecord[] {
  if (sourceType !== "act") return events.map(decorate);

  const buckets = new Map<string, SourceEventRecord[]>();
  for (const event of events) {
    const key = presentationBucketKey(event);
    const bucket = buckets.get(key) || [];
    bucket.push(event);
    buckets.set(key, bucket);
  }

  return [...buckets.values()].map((bucket) => {
    const selected = [...bucket].sort((first, second) => eventQuality(second) - eventQuality(first) || text(first.id).localeCompare(text(second.id)))[0];
    const participantEvent = [...bucket].sort((first, second) => {
      return participantIdentityQuality(second, occurrencesById) - participantIdentityQuality(first, occurrencesById)
        || eventQuality(second) - eventQuality(first)
        || text(first.id).localeCompare(text(second.id));
    })[0];
    return {
      ...selected,
      participants: participantEvent.participants,
      groupingCandidateId: selected.groupingCandidateId,
      groupingStatus: selected.groupingStatus,
      groupingFingerprint: selected.groupingFingerprint,
      backingEventIds: [...new Set(bucket.flatMap((event) => backingValues(event, "mergedEventIds", event.id)))],
      originalTypes: [...new Set(bucket.flatMap((event) => backingValues(event, "originalTypes", event.type)))],
      originalPlaces: [...new Set(bucket.flatMap((event) => backingValues(event, "originalPlaces", event.place)))],
      presentationGrouping: bucket.length > 1 && text(selected.groupingStatus) !== "resolved_same_record"
        ? "candidate_variants"
        : text(selected.groupingStatus) === "resolved_same_record"
          ? "resolved_event"
          : "single_event",
      variantEvents: bucket.length > 1 && text(selected.groupingStatus) !== "resolved_same_record"
        ? bucket.map(decorate)
        : [],
    };
  });
}
