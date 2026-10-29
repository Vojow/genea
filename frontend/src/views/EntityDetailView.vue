<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { RouterLink } from "vue-router";

import type { EntityReference } from "../routing/entities";
import { EntityIndex } from "../routing/entities";
import { useAppStore } from "../stores/app";
import type { EntityKind } from "../router";
import type { PresentationTemporalConstraint } from "../types/presentation";
import { parseResearchQuestionAssessment, researchAssessmentDispositionLabels } from "../types/presentation";
import { workspaceLabelForEntity } from "../navigation/workspaces";
import { projectSourceEvents } from "../models/source-event-projector";
import { projectSourceRecordSummary } from "../models/source-record-summary";
import { projectSourceIdentityGroups, sourceIdentityPath, sourceOccurrencePath } from "../models/source-identity-groups";
import EntityBreadcrumbs from "../components/EntityBreadcrumbs.vue";
import EntityLink from "../components/EntityLink.vue";
import EntityPageHeader from "../components/EntityPageHeader.vue";
import EventRecordCurationPanel from "../components/EventRecordCurationPanel.vue";
import SourceDocumentReader from "../components/SourceDocumentReader.vue";
import SourceReviewPanel from "../components/SourceReviewPanel.vue";
import VolumeCandidateList from "../components/VolumeCandidateList.vue";
import { eventTypeLabel, polishCount, polishDate, roleLabel, statusLabel } from "../utils/formatting";
import PersonDetailView from "./person/PersonDetailView.vue";


const props = defineProps<{ entity: EntityReference }>();
const store = useAppStore();
const index = computed(() => new EntityIndex(store.payload!));
const raw = computed(() => props.entity.raw || {});
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const text = (value: unknown): string => value == null ? "" : String(value);
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" ? value as Record<string, unknown> : {};
const eventGroupingNote = (event: Record<string, unknown>): string => {
  const status = text(event.groupingStatus);
  if (status === "resolved_same_record") return "Kurator potwierdził, że są to techniczne zapisy tego samego zdarzenia.";
  if (status === "proposed") return "To zwinięta grupa możliwych wariantów. Rekordy pozostają osobne do decyzji kuratora.";
  if (status === "deferred") return "Kurator odroczył decyzję; ten rekord pozostaje osobnym zdarzeniem.";
  if (status === "resolved_distinct") return "Kurator potwierdził, że są to odrębne zdarzenia.";
  return "To zwinięta grupa technicznych wariantów wykrytych w jednym akcie. Nie oznacza jeszcze, że opisują jedno zdarzenie.";
};
const isCandidateVariantGroup = (event: Record<string, unknown>): boolean => event.presentationGrouping === "candidate_variants";
const reloadAfterEventCuration = (): void => window.location.reload();

const source = computed(() => props.entity.kind === "source" ? store.payload?.sources.find((item) => item.id === props.entity.id) : null);
const sourceIdentityGroups = computed(() => source.value && store.payload ? projectSourceIdentityGroups(store.payload).filter((group) => group.sourceItemIds.includes(source.value!.id)) : []);
const volume = computed(() => props.entity.kind === "volume" ? store.payload?.volumeIndexes?.find((item) => item.id === props.entity.id) : null);
const run = computed(() => props.entity.kind === "run" ? index.value.findRun(props.entity.id) : null);
const caseQuestions = computed(() => props.entity.kind === "case" ? list(raw.value.questions).map(record) : []);
const caseActions = computed(() => props.entity.kind === "case" ? list(raw.value.nextActions).map(record).filter((action) => action.status === "open") : []);
const caseQuestionLabel = (question: Record<string, unknown>): string => question.origin === "deterministic"
  ? `Kandydat pytania · ${text(question.question)}`
  : text(question.question);
const caseTargets = computed(() => {
  const seen = new Set<string>();
  return caseQuestions.value.flatMap((question) => list(question.targets).map(record)).filter((target) => {
    const key = `${text(target.type)}:${text(target.id)}`;
    if (!text(target.id) || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
});
const questionTargets = computed(() => props.entity.kind === "question" ? list(raw.value.targets).map(record) : []);
const questionAssessment = computed(() => props.entity.kind === "question" ? parseResearchQuestionAssessment(raw.value.assessment) : null);
const questionHasAssessment = computed(() => props.entity.kind === "question" && Object.hasOwn(raw.value, "assessment"));
const questionExecution = computed(() => props.entity.kind === "question" ? record(raw.value.executionPlan) : {});
const questionArchiveQueries = computed(() => list(questionExecution.value.archiveQueries).map(record));
const questionBatches = computed(() => list(questionExecution.value.batches).map(record));
const questionActiveBatches = computed(() => questionBatches.value.filter((batch) => !["completed", "inconclusive", "superseded"].includes(text(batch.status))));
const questionBlockers = computed(() => list(raw.value.blockers || questionExecution.value.blockers).map(text).filter(Boolean));
const questionCost = computed(() => {
  const telemetry = record(questionExecution.value.telemetry);
  const value = raw.value.estimatedCost ?? telemetry.cost ?? questionExecution.value.estimatedCost;
  const provenance = text(raw.value.costProvenance || telemetry.costProvenance || questionExecution.value.costProvenance);
  return value == null || value === "" ? "nieznany" : `${text(value)}${provenance ? ` · ${provenance}` : ""}`;
});
const eventSource = computed(() => props.entity.kind === "event" ? index.value.resolve("source", text(raw.value.sourceId)) : null);
const placeObservations = computed(() => props.entity.kind === "place"
  ? [
    ...list(store.payload?.map?.observations),
    ...list(store.payload?.map?.sourceObservations),
  ].map(record).filter((item) => item.placeId === props.entity.id)
  : []);
const occurrenceById = computed(() => new Map(list(store.payload?.occurrences).map(record).map((item) => [text(item.id), item])));
const sourceEvents = computed(() => props.entity.kind === "source" ? list(store.payload?.events).map(record).filter((item) => item.sourceId === props.entity.id) : []);
const sourceEventGroups = computed(() => projectSourceEvents(sourceEvents.value, source.value?.type, occurrenceById.value));
const sourceTemporalConstraints = computed(() => props.entity.kind === "source"
  ? (store.payload?.temporalConstraints || []).filter((item) => item.sourceId === props.entity.id)
  : []);
const sourceRecordSummary = computed(() => source.value ? projectSourceRecordSummary(source.value, sourceEventGroups.value) : null);
const lineFamilyHouseholds = computed(() => props.entity.kind === "line" ? list(raw.value.familyHouseholds).map(record) : []);
const lineDirectFamilyHouseholds = computed(() => lineFamilyHouseholds.value.filter((item) => item.scopeRelation !== "collateral_descendant"));
const lineCollateralFamilyHouseholds = computed(() => lineFamilyHouseholds.value.filter((item) => item.scopeRelation === "collateral_descendant"));
const lineSourceHouseholds = computed(() => props.entity.kind === "line"
  ? list(raw.value.sourceHouseholdIds).map(text).map((id) => index.value.resolve("household", id)).filter((item) => item.availability !== "missing")
  : []);
const householdMembers = computed(() => props.entity.kind === "household" ? list(raw.value.members).map(record) : []);
const householdIsReconstruction = computed(() => props.entity.kind === "household" && raw.value.kind === "family_reconstruction");
const placeEventGroups = computed(() => {
  const groups = new Map<string, {
    id: string;
    eventId: string;
    sourceId: string;
    eventType: string;
    date: string;
    status: string;
    people: Array<{ key: string; id: string; label: string; role: string; occurrenceId: string }>;
  }>();
  for (const observation of placeObservations.value) {
    const eventId = text(observation.eventId);
    const sourceId = text(observation.sourceId);
    const eventType = text(observation.eventType || observation.kind);
    const date = text(observation.dateFrom || observation.dateTo || observation.yearFrom || observation.yearTo);
    const key = eventId || `${sourceId}:${eventType}:${date}` || text(observation.id);
    const group = groups.get(key) || {
      id: key,
      eventId,
      sourceId,
      eventType,
      date,
      status: text(observation.eventStatus),
      people: [],
    };
    const personId = text(observation.personId);
    const occurrenceId = text(observation.occurrenceId);
    const occurrence = occurrenceById.value.get(occurrenceId);
    const role = text(observation.role);
    const personKey = `${personId || `occurrence:${occurrenceId || text(observation.id)}`}:${role}`;
    if (!group.people.some((person) => person.key === personKey)) {
      group.people.push({
        key: personKey,
        id: personId,
        label: text(observation.personLabel || observation.occurrenceLabel || observation.rawName || occurrence?.name || occurrence?.normalizedName || occurrence?.rawName || occurrenceId) || "Wystąpienie źródłowe",
        role,
        occurrenceId,
      });
    }
    groups.set(key, group);
  }
  return [...groups.values()].sort((first, second) => (first.date || "9999").localeCompare(second.date || "9999", "pl-PL"));
});

function participantPerson(participant: Record<string, unknown>): EntityReference | null {
  const occurrence = occurrenceById.value.get(text(participant.occurrenceId));
  const occurrenceContext = index.value.personContexts.byOccurrenceId.get(text(participant.occurrenceId));
  const canonicalContextPersonId = occurrenceContext?.identityScope === "canonical" ? occurrenceContext.personKey : "";
  const personId = canonicalContextPersonId || text(participant.personId || occurrence?.personId);
  if (!personId) return null;
  const resolved = index.value.resolve("person", personId);
  return resolved.availability === "missing" ? null : resolved;
}

function participantLabel(participant: Record<string, unknown>): string {
  const occurrence = occurrenceById.value.get(text(participant.occurrenceId));
  return text(occurrence?.name || occurrence?.normalizedName || occurrence?.rawName) || "Osoba wymieniona w akcie";
}

function admittedParticipantIdentityLabel(participant: Record<string, unknown>, occurrence: Record<string, unknown> | undefined, person: EntityReference | null): string {
  const identifier = (value: unknown): value is string => typeof value === "string" && Boolean(value) && !/[\s/\\]/.test(value);
  const hidden = (item: Record<string, unknown> | undefined): boolean => item?.restrictedBacking === true || item?.sourceRedacted === true
    || ["restricted", "living", "private", "family_private", "masked_living"].includes(text(typeof item?.privacy === "string" ? item.privacy : record(item?.privacy).status));
  if (!occurrence || !person || person.availability !== "found" || hidden(occurrence) || hidden(person.raw) || hidden(source.value as unknown as Record<string, unknown>)) return "";
  const personId = occurrence.personId;
  const backingPerson = list(store.payload?.people).map(record).find((item) => item.id === personId);
  if (!identifier(occurrence.id) || !identifier(personId) || !backingPerson || hidden(backingPerson)
    || (backingPerson.availability != null && backingPerson.availability !== "found")
    || index.value.resolve("person", personId).id !== person.id
    || (participant.personId !== undefined && participant.personId !== personId)
    || occurrence.sourceId !== props.entity.id) return "";
  const tier = occurrence.identityDecisionTier;
  const chains = occurrence.sourceProfileIdentityDirectChains;
  if (occurrence.sourceProfileIdentityGuarded !== true || occurrence.personIdentityResolved !== true
    || occurrence.identityStatus !== "resolved_same" || !["agent_approved_human_pending", "human_confirmed"].includes(text(tier))
    || !Array.isArray(chains) || !chains.length
    || !chains.every((chain) => chain && typeof chain === "object" && !Array.isArray(chain)
      && [chain.candidateId, chain.commandId, chain.decisionId].every(identifier))) return "";
  // Event participants may carry a smaller projection; any supplied decision flags must agree with the occurrence DTO.
  if (["sourceProfileIdentityGuarded", "personIdentityResolved", "identityStatus", "identityDecisionTier"].some((field) => (
    participant[field] !== undefined && participant[field] !== occurrence[field]
  ))) return "";
  return tier === "agent_approved_human_pending"
    ? "tożsamość zatwierdzona przez agenta · oczekuje na potwierdzenie człowieka"
    : "tożsamość potwierdzona przez człowieka";
}

function sourceEventParticipants(event: Record<string, unknown>): Array<{
  key: string;
  person: EntityReference | null;
  label: string;
  roles: string[];
  occurrenceId: string;
  candidateLabel: string;
  vitalStatusLabel: string;
  temporalConstraints: PresentationTemporalConstraint[];
}> {
  const grouped = new Map<string, {
    key: string;
    person: EntityReference | null;
    label: string;
    roles: string[];
    occurrenceId: string;
    candidateLabel: string;
    vitalStatusLabel: string;
    temporalConstraints: PresentationTemporalConstraint[];
  }>();
  const backingEventIds = new Set([text(event.id), ...list(event.backingEventIds).map(text)].filter(Boolean));
  for (const participant of list(event.participants).map(record)) {
    const occurrenceId = text(participant.occurrenceId);
    const occurrence = occurrenceById.value.get(occurrenceId);
    const person = participantPerson(participant);
    const label = person?.label || text(occurrence?.name || occurrence?.normalizedName || occurrence?.rawName) || "Osoba wymieniona w akcie";
    const key = person ? `person:${person.id}` : `occurrence:${occurrenceId || label.toLocaleLowerCase("pl-PL")}`;
    const candidateStatus = text(participant.candidatePersonStatus || occurrence?.candidatePersonStatus);
    const admittedIdentityLabel = admittedParticipantIdentityLabel(participant, occurrence, person);
    const explicitlyUnresolved = !admittedIdentityLabel && (participant.personIdentityResolved === false
      || occurrence?.personIdentityResolved === false
      || ["candidate", "candidate_unresolved", "unresolved"].includes(candidateStatus)
      || occurrence?.sourceProfileIdentityGuarded != null);
    const candidateLabel = explicitlyUnresolved ? "osoba kandydacka · tożsamość nierozstrzygnięta" : admittedIdentityLabel;
    const vitalStatus = record(participant.vitalStatus || occurrence?.vitalStatus);
    const vitalStatusCode = text(vitalStatus.code || participant.vitalStatus || occurrence?.vitalStatus);
    const vitalStatusLabel = text(vitalStatus.label) || ({
      deceased: "osoba zmarła",
      living: "osoba żyjąca",
      unknown: "stan życia nieustalony",
    } as Record<string, string>)[vitalStatusCode] || "";
    const temporalConstraints = sourceTemporalConstraints.value.filter((constraint) => (
      constraint.occurrenceId === occurrenceId
      && backingEventIds.has(constraint.eventId)
    ));
    const existing = grouped.get(key) || {
      key,
      person,
      label,
      roles: [],
      occurrenceId,
      candidateLabel,
      vitalStatusLabel,
      temporalConstraints,
    };
    const role = roleLabel(text(participant.role || occurrence?.role));
    if (role && !existing.roles.includes(role)) existing.roles.push(role);
    if (explicitlyUnresolved || !existing.candidateLabel) existing.candidateLabel = candidateLabel;
    if (!existing.vitalStatusLabel && vitalStatusLabel) existing.vitalStatusLabel = vitalStatusLabel;
    for (const constraint of temporalConstraints) {
      if (!existing.temporalConstraints.some((item) => item.id === constraint.id)) existing.temporalConstraints.push(constraint);
    }
    grouped.set(key, existing);
  }
  return [...grouped.values()];
}

function temporalConstraintLabel(constraint: PresentationTemporalConstraint): string {
  if (constraint.label) return constraint.label;
  const eventLabel = constraint.vitalEventType === "death" ? "zgon" : eventTypeLabel(constraint.vitalEventType);
  const relation = ({
    before: "przed",
    on_or_before: "nie później niż",
    after: "po",
    on_or_after: "nie wcześniej niż",
  } as Record<string, string>)[constraint.relation] || constraint.relation;
  return `${eventLabel} ${relation} ${polishDate(constraint.boundaryDate)}`;
}

function temporalConstraintBasis(constraint: PresentationTemporalConstraint): string {
  return [constraint.sourceAssertionId && `asercja ${constraint.sourceAssertionId}`, constraint.eventId && `zdarzenie ${constraint.eventId}`]
    .filter(Boolean)
    .join(" · ");
}

function sourceEventPlace(event: Record<string, unknown>): EntityReference | null {
  const placeId = text(event.placeId);
  if (!placeId) return null;
  const resolved = index.value.resolve("place", placeId);
  return resolved.availability === "found" ? resolved : null;
}

function householdMemberPerson(member: Record<string, unknown>): EntityReference | null {
  const occurrenceContext = index.value.personContexts.byOccurrenceId.get(text(member.occurrenceId));
  const personId = text(member.treePersonId || member.personId) || occurrenceContext?.personKey || "";
  if (!personId) return null;
  const resolved = index.value.resolve("person", personId);
  return resolved.availability === "missing" ? null : resolved;
}

function targetKind(value: unknown): EntityKind | null {
  const aliases: Record<string, EntityKind> = { person: "person", line: "line", place: "place", household: "household", event: "event", source: "source", case: "case", question: "question", volume: "volume", run: "run", family_cluster: "cluster", cluster: "cluster" };
  return aliases[text(value)] || null;
}

function remember(): void {
  if (props.entity.kind !== "person" && props.entity.availability === "found" && !raw.value.private) store.recordRecent({
    kind: props.entity.kind,
    id: props.entity.id,
    label: props.entity.label,
  });
}

function archiveQuerySummary(query: Record<string, unknown>): string {
  const specification = record(query.specification);
  return Object.entries(specification)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : text(value)}`)
    .join(" · ");
}

function sourceBasisLabel(basis?: string | null): string {
  const labels: Record<string, string> = {
    "source_item.date_text": "zapisane w metryce źródła",
    "linked_event.date_from": "z powiązanego zdarzenia",
    "source_item.metadata": "z metadanych źródła",
    "source_item.title": "rozpoznane w tytule źródła",
  };
  return labels[basis || ""] || "podstawa nieopisana";
}

function missingSourceFieldLabel(field: string): string {
  return field === "source_date" ? "data źródła" : field === "source_locator" ? "kanoniczny lokalizator" : field;
}

onMounted(remember);
watch(() => props.entity.id, remember);
</script>

<template>
  <PersonDetailView v-if="entity.kind === 'person'" :entity="entity" />
  <article v-else class="entity-detail-view">
    <EntityBreadcrumbs :workspace="workspaceLabelForEntity(entity.kind)" :kind="entity.kind" :label="entity.label" />
    <EntityPageHeader :kind="entity.kind" :label="entity.label" :status="entity.status" />

    <p v-if="entity.kind === 'cluster'" class="method-note">Klaster jest agregatem prezentacyjnym. Kandydatury identity nie stają się przez niego potwierdzonymi osobami.</p>
    <p v-if="entity.kind === 'event' || entity.kind === 'source'" class="method-note">Źródło nie jest dowodem. Odczyt, informacja źródłowa i wniosek badacza pozostają rozdzielone.</p>

    <div class="entity-detail-grid" :class="`is-${entity.kind}`">
      <section v-if="entity.kind === 'line'"><h2>Zakres linii</h2><p>{{ text(raw.description) }}</p><dl><dt>Rdzeń źródłowy</dt><dd>{{ list(raw.seedOccurrenceIds).length }} wystąpień</dd><dt>Gospodarstwa powiązane ze źródłami</dt><dd>{{ lineSourceHouseholds.length }}</dd><dt>Rodziny przy linii</dt><dd>{{ lineDirectFamilyHouseholds.length }}</dd><dt>Boczne rodziny potomne</dt><dd>{{ lineCollateralFamilyHouseholds.length }}</dd></dl></section>
      <section v-if="entity.kind === 'line'"><h2>Przejścia</h2><RouterLink to="/tree">Otwórz drzewo</RouterLink><RouterLink to="/map">Otwórz mapę</RouterLink><RouterLink to="/graph">Otwórz lokalny FAN</RouterLink></section>
      <section v-if="entity.kind === 'line'" class="line-households"><header class="detail-section-heading"><p class="eyebrow">Struktura rodziny i zamieszkanie</p><h2>Gospodarstwa</h2></header><p class="method-note">Małżeństwo lub para z dziećmi tworzy gospodarstwo rekonstruowane, ale nie dowodzi wspólnego adresu. Zakres obejmuje również znane rodziny rodzeństwa bezpośrednich przodków i ich dalszych potomków. Nie przechodzi do przodków ich małżonków.</p><h3>Powiązane ze źródłami · {{ lineSourceHouseholds.length }}</h3><div class="line-household-list"><EntityLink v-for="household in lineSourceHouseholds" :key="household.id" kind="household" :id="household.id" :label="household.label" :status="household.status" /><p v-if="!lineSourceHouseholds.length" class="empty-state-card">Brak jawnie powiązanego gospodarstwa źródłowego w tym zakresie.</p></div><h3>Przy bezpośredniej linii · {{ lineDirectFamilyHouseholds.length }}</h3><div class="line-household-list"><article v-for="household in lineDirectFamilyHouseholds" :key="text(household.id)" class="line-household-card"><EntityLink kind="household" :id="text(household.id)" :label="text(household.label)" :status="text(household.status)" /><p>{{ polishCount(list(household.parentIds).length, "rodzic", "rodziców", "rodziców") }} · {{ polishCount(list(household.childIds).length, "dziecko", "dzieci", "dzieci") }}</p><small>{{ text(household.evidenceNeeded) }}</small></article><p v-if="!lineDirectFamilyHouseholds.length" class="empty-state-card">Brak układu co najmniej dwojga rodziców i dziecka przy bezpośredniej linii.</p></div><h3>Rodzeństwo przodków i jego potomkowie · {{ lineCollateralFamilyHouseholds.length }}</h3><div class="line-household-list"><article v-for="household in lineCollateralFamilyHouseholds" :key="text(household.id)" class="line-household-card is-collateral"><span class="line-household-scope">Gałąź boczna od: {{ list(household.anchorLabels).map(text).join(', ') }}</span><EntityLink kind="household" :id="text(household.id)" :label="text(household.label)" :status="text(household.status)" /><p>{{ polishCount(list(household.parentIds).length, "rodzic", "rodziców", "rodziców") }} · {{ polishCount(list(household.childIds).length, "dziecko", "dzieci", "dzieci") }}</p><small>{{ text(household.evidenceNeeded) }}</small></article><p v-if="!lineCollateralFamilyHouseholds.length" class="empty-state-card">Nie zapisano jeszcze dalszych rodzin rodzeństwa osób z tej linii.</p></div></section>

      <section v-if="entity.kind === 'place'"><h2>Nazwy i jurysdykcja</h2><p>{{ list(raw.aliases).map(text).join(', ') || "Brak dodatkowych nazw." }}</p><dl><dt>Precyzja</dt><dd>{{ text(raw.precision) || "unknown" }}</dd><dt>Coverage</dt><dd>{{ text(raw.coverage) || "nieprzeszukane / brak pełnej macierzy w eksporcie" }}</dd></dl></section>
      <section v-if="entity.kind === 'place'">
        <header class="detail-section-heading"><p class="eyebrow">Oś miejsca</p><h2>Zdarzenia i źródła</h2></header>
        <template v-for="group in placeEventGroups.slice(0, 10)" :key="group.id">
          <article class="place-event-row">
            <div class="place-event-heading">
              <div><strong>{{ eventTypeLabel(group.eventType) }}</strong><span>{{ polishDate(group.date) }}</span></div>
              <span class="entity-status">{{ statusLabel(group.status) }}</span>
            </div>
            <div v-for="person in group.people" :key="person.key" class="place-event-participant">
              <EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :source-id="group.sourceId" :occurrence-id="person.occurrenceId" />
              <strong v-else>{{ person.label }}</strong>
              <small>{{ roleLabel(person.role) || "rola nieopisana" }}</small>
              <small v-if="person.occurrenceId">Wystąpienie: {{ person.occurrenceId }}</small>
            </div>
            <div class="place-event-actions">
              <EntityLink v-if="group.eventId && index.resolve('event', group.eventId).availability !== 'missing'" kind="event" :id="group.eventId" :label="index.resolve('event', group.eventId).label" />
              <EntityLink v-if="group.sourceId" kind="source" :id="group.sourceId" :label="index.resolve('source', group.sourceId).label" />
              <small v-if="group.sourceId">Źródło: {{ group.sourceId }}</small>
            </div>
          </article>
        </template>
        <details v-if="placeEventGroups.length > 10">
          <summary>Pokaż pozostałe zdarzenia ({{ placeEventGroups.length - 10 }})</summary>
          <article v-for="group in placeEventGroups.slice(10)" :key="group.id" class="place-event-row">
            <div class="place-event-heading">
              <div><strong>{{ eventTypeLabel(group.eventType) }}</strong><span>{{ polishDate(group.date) }}</span></div>
              <span class="entity-status">{{ statusLabel(group.status) }}</span>
            </div>
            <div v-for="person in group.people" :key="person.key" class="place-event-participant">
              <EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :source-id="group.sourceId" :occurrence-id="person.occurrenceId" />
              <strong v-else>{{ person.label }}</strong>
              <small>{{ roleLabel(person.role) || "rola nieopisana" }}</small>
              <small v-if="person.occurrenceId">Wystąpienie: {{ person.occurrenceId }}</small>
            </div>
            <div class="place-event-actions">
              <EntityLink v-if="group.eventId && index.resolve('event', group.eventId).availability !== 'missing'" kind="event" :id="group.eventId" :label="index.resolve('event', group.eventId).label" />
              <EntityLink v-if="group.sourceId" kind="source" :id="group.sourceId" :label="index.resolve('source', group.sourceId).label" />
              <small v-if="group.sourceId">Źródło: {{ group.sourceId }}</small>
            </div>
          </article>
        </details>
        <p v-if="!placeEventGroups.length" class="empty-state-card">Brak zdarzeń i wystąpień źródłowych powiązanych z tym miejscem.</p>
      </section>

      <section v-if="entity.kind === 'event'"><h2>Zdarzenie</h2><dl><dt>Typ</dt><dd>{{ eventTypeLabel(text(raw.type)) }}</dd><dt>Data</dt><dd>{{ polishDate(text(raw.date || raw.dateFrom)) }}</dd><dt>Miejsce</dt><dd><EntityLink v-if="raw.placeId" kind="place" :id="text(raw.placeId)" :label="index.resolve('place', text(raw.placeId)).label" /><span v-else>{{ text(raw.place) }}</span></dd><dt>Status</dt><dd>{{ statusLabel(text(raw.status)) }}</dd></dl><EntityLink v-if="eventSource?.id" kind="source" :id="eventSource.id" :label="eventSource.label" /></section>
      <section v-if="entity.kind === 'event'"><h2>Role</h2><article v-for="participant in list(raw.participants).map(record)" :key="text(participant.occurrenceId || participant.personId)" class="event-participant"><span>{{ roleLabel(text(participant.role)) }}</span><EntityLink v-if="participantPerson(participant)" kind="person" :id="participantPerson(participant)!.id" :label="participantPerson(participant)!.label" :occurrence-id="text(participant.occurrenceId)" :source-id="text(raw.sourceId)" /><span v-else>{{ participantLabel(participant) }}</span></article></section>

      <section v-if="entity.kind === 'source' && source" class="source-reader-section"><SourceDocumentReader :source="source" /></section>
      <details v-if="entity.kind === 'source' && source && sourceRecordSummary" class="source-record-metadata source-record-metadata-details">
        <summary>Metryka źródła i pochodzenie</summary>
        <section>
        <header class="detail-section-heading"><p class="eyebrow">Metryka źródła</p><h2>Opis i pochodzenie</h2></header>
        <p class="source-metadata-intro">Pola zapisane bezpośrednio w metryce są oddzielone od informacji wyprowadzonych z powiązanego zdarzenia, tytułu i cytowań.</p>
        <dl class="source-record-facts">
          <div><dt>Rodzaj materiału</dt><dd>{{ sourceRecordSummary.typeLabel }}</dd></div>
          <div><dt>Numer aktu</dt><dd>{{ sourceRecordSummary.recordNumber || "nie wyodrębniono" }}<small v-if="sourceRecordSummary.recordNumber">{{ sourceBasisLabel(sourceRecordSummary.recordNumberBasis) }}</small></dd></div>
          <div><dt>Data aktu</dt><dd>{{ sourceRecordSummary.date ? polishDate(sourceRecordSummary.date) : "nie wyodrębniono" }}<small v-if="sourceRecordSummary.date">{{ sourceBasisLabel(sourceRecordSummary.dateBasis) }}</small></dd></div>
          <div><dt>Miejsca wymienione</dt><dd>{{ sourceRecordSummary.places.join(' · ') || "nie wyodrębniono" }}</dd></div>
          <div><dt>Repozytorium</dt><dd>{{ sourceRecordSummary.repository || "nie zapisano" }}</dd></div>
          <div><dt>Status opracowania</dt><dd>{{ statusLabel(source.status) }}</dd></div>
          <div><dt>Uczestnicy zdarzenia</dt><dd>{{ sourceRecordSummary.participantCount }}</dd></div>
          <div><dt>Materiały lokalne</dt><dd>{{ source.artifactCount || 0 }}</dd></div>
          <div class="is-wide"><dt>Kanoniczny lokalizator</dt><dd>{{ sourceRecordSummary.canonicalLocator || "nie zapisano" }}</dd></div>
          <div class="is-wide"><dt>Lokalizatory cytowań</dt><dd>{{ sourceRecordSummary.citationLocators.join(' · ') || "brak" }}</dd></div>
        </dl>
        <p v-if="sourceRecordSummary.missingCanonicalFields.length" class="source-metadata-gap"><strong>Do uzupełnienia w metryce źródła:</strong> {{ sourceRecordSummary.missingCanonicalFields.map(missingSourceFieldLabel).join(', ') }}. Dane wyprowadzone powyżej pozostają oznaczone swoją podstawą.</p>
        <a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">Otwórz materiał w serwisie archiwalnym ↗</a>
        </section>
      </details>
      <section v-if="entity.kind === 'source' && source" class="source-context-section">
        <header class="detail-section-heading"><p class="eyebrow">Struktura aktu</p><h2>Zdarzenie i osoby</h2></header>
        <p class="method-note">To uporządkowany kontekst odczytu. Nierozstrzygnięte rekordy mogą być zwinięte w grupę możliwych wariantów, lecz pozostają osobnymi zdarzeniami aż do jawnej decyzji kuratora.</p>
        <article v-for="event in sourceEventGroups" :key="text(event.id)" class="source-event-context-card">
          <header class="source-event-context-heading">
            <div><p class="eyebrow">{{ polishDate(text(event.dateFrom || event.date)) }}</p><h3>{{ eventTypeLabel(text(event.type)) }}</h3></div>
            <EntityLink class="source-event-link" kind="event" :id="text(event.id)" :label="eventTypeLabel(text(event.type))" :status="text(event.status)" />
          </header>
          <dl class="source-event-summary">
            <div><dt>Miejsce</dt><dd><EntityLink v-if="sourceEventPlace(event)" kind="place" :id="sourceEventPlace(event)!.id" :label="sourceEventPlace(event)!.label" /><span v-else>{{ text(event.place) || "miejsce nieustalone" }}</span></dd></div>
            <div><dt>Uczestnicy</dt><dd>{{ sourceEventParticipants(event).length }}</dd></div>
          </dl>
          <div class="source-participant-grid">
            <article v-for="participant in sourceEventParticipants(event)" :key="participant.key" class="source-participant-card">
              <EntityLink v-if="participant.person" kind="person" :id="participant.person.id" :label="participant.person.label" :occurrence-id="participant.occurrenceId" :source-id="source.id" />
              <strong v-else>{{ participant.label }}</strong>
              <span v-if="participant.candidateLabel" class="source-occurrence-status">{{ participant.candidateLabel }}</span>
              <span v-else-if="!participant.person" class="source-occurrence-status">publiczne wystąpienie źródłowe · bez decyzji tożsamości</span>
              <small>{{ participant.roles.join(' · ') || "rola nieopisana" }}</small>
              <small v-if="participant.vitalStatusLabel"><strong>W źródle:</strong> {{ participant.vitalStatusLabel }}</small>
              <small v-for="constraint in participant.temporalConstraints" :key="constraint.id">
                <strong>Granica:</strong> {{ temporalConstraintLabel(constraint) }}
                <span v-if="temporalConstraintBasis(constraint)"> · {{ temporalConstraintBasis(constraint) }}</span>
              </small>
            </article>
          </div>
          <details v-if="list(event.backingEventIds).length > 1 || event.groupingCandidateId" class="source-event-provenance">
            <summary>Status grupowania rekordów zdarzenia</summary>
            <p v-if="isCandidateVariantGroup(event)">Zwinięto w jednej karcie {{ list(event.backingEventIds).length }} możliwe warianty: {{ list(event.originalTypes).map(text).join(', ') }}. Nie są one scalone w modelu zdarzeń.</p>
            <p v-else-if="list(event.backingEventIds).length > 1">Po decyzji kuratora pokazano jako jedno zdarzenie {{ list(event.backingEventIds).length }} rekordy techniczne: {{ list(event.originalTypes).map(text).join(', ') }}.</p>
            <p v-else>Ten rekord należy do kandydatury, którą kurator rozstrzygnął jako odrębne zdarzenia.</p>
            <p>{{ eventGroupingNote(event) }}</p>
            <ul v-if="isCandidateVariantGroup(event)" class="source-event-variant-list">
              <li v-for="variant in list(event.variantEvents).map(record)" :key="text(variant.id)">
                <EntityLink kind="event" :id="text(variant.id)" :label="eventTypeLabel(text(variant.type))" :status="text(variant.status)" />
                <span>{{ polishDate(text(variant.dateFrom || variant.date)) }}</span>
                <small>{{ text(variant.id) }}</small>
              </li>
            </ul>
            <small v-if="event.groupingCandidateId">Kandydatura kuratorska: {{ text(event.groupingCandidateId) }}</small>
            <small>Backing event IDs: {{ list(event.backingEventIds).map(text).join(', ') }}</small>
            <EventRecordCurationPanel
              v-if="event.groupingCandidateId"
              :candidate-id="text(event.groupingCandidateId)"
              :access="store.curatorAccess"
              @resolved="reloadAfterEventCuration"
            />
          </details>
        </article>
        <p v-if="!sourceEventGroups.length" class="empty-state-card">Nie zapisano jeszcze ustrukturyzowanego zdarzenia dla tego aktu.</p>
      </section>
      <section v-if="sourceIdentityGroups.length" class="source-identity-groups">
        <h2>Powiązane wystąpienia źródłowe</h2>
        <article v-for="group in sourceIdentityGroups" :key="group.id" :data-source-identity-id="group.id">
          <p>Tożsamość zatwierdzona przez agenta · oczekuje na potwierdzenie człowieka.</p>
          <RouterLink :to="sourceIdentityPath(group.id)" @click="store.captureNavigationContext()">Otwórz grupę wystąpień źródłowych ({{ group.members.length }})</RouterLink>
          <ul><li v-for="member in group.members" :key="member.occurrenceId">
            <RouterLink :to="sourceOccurrencePath(member.occurrenceId)" @click="store.captureNavigationContext()">{{ member.literalName }}</RouterLink>
            <EntityLink kind="source" :id="member.sourceId" :label="member.sourceTitle" :source-id="member.sourceId" :occurrence-id="member.occurrenceId" />
          </li></ul>
        </article>
      </section>
      <SourceReviewPanel v-if="entity.kind === 'source' && source" :source="source" />

      <section v-if="entity.kind === 'household'"><h2>{{ householdIsReconstruction ? "Rekonstrukcja rodziny" : "Mieszkańcy i okres" }}</h2><p v-if="householdIsReconstruction" class="method-note">To kandydat gospodarstwa utworzony ze struktury rodzice–dzieci. Nie potwierdza wspólnego zamieszkania ani adresu.</p><dl><dt>Podstawa</dt><dd>{{ householdIsReconstruction ? "kuratorowana struktura rodziny" : "rekord gospodarstwa" }}</dd><dt>Wspólny adres</dt><dd>{{ householdIsReconstruction ? "do potwierdzenia dokumentem" : text(raw.houseNumber || raw.address || raw.placeId) || "nieustalony" }}</dd><dt>Od–do</dt><dd>{{ text(raw.dateFrom) || "?" }}–{{ text(raw.dateTo) || "?" }}</dd><dt>Status</dt><dd>{{ statusLabel(text(raw.status)) }}</dd></dl><h3>Członkowie · {{ householdMembers.length || list(raw.personIds || raw.memberPersonIds).length }}</h3><template v-if="householdMembers.length"><p v-for="member in householdMembers" :key="`${text(member.treePersonId || member.personId || member.occurrenceId)}:${text(member.role)}`" class="household-member-row"><span>{{ roleLabel(text(member.role)) }}</span><EntityLink v-if="householdMemberPerson(member)" kind="person" :id="householdMemberPerson(member)!.id" :label="householdMemberPerson(member)!.label" :occurrence-id="text(member.occurrenceId)" /><strong v-else>{{ text(member.label || member.occurrenceId || member.personId) }}</strong></p></template><template v-else><template v-for="id in list(raw.personIds || raw.memberPersonIds).map(text)" :key="id"><EntityLink v-if="index.resolve('person', id).availability !== 'missing'" kind="person" :id="id" :label="index.resolve('person', id).label" /><p v-else>{{ id }} · occurrence lub osoba poza eksportem</p></template></template><p v-if="householdIsReconstruction"><strong>Do potwierdzenia:</strong> {{ text(raw.evidenceNeeded) }}</p></section>
      <section v-if="entity.kind === 'cluster'"><h2>Podstawa agregacji</h2><dl><dt>Typ</dt><dd>{{ text(raw.basis) || "network" }}</dd><dt>Elementy podstawowe</dt><dd>{{ list(raw.backingNodeIds).length }}</dd><dt>Krawędzie podstawowe</dt><dd>{{ list(raw.backingEdgeIds).length }}</dd></dl><EntityLink v-if="raw.householdId" kind="household" :id="text(raw.householdId)" :label="index.resolve('household', text(raw.householdId)).label" /></section>

      <section v-if="entity.kind === 'case'" class="case-overview">
        <h2>Podsumowanie sprawy</h2>
        <p class="case-summary">{{ text(raw.summary) || "Brak podsumowania sprawy w tym eksporcie." }}</p>
        <h3>Aktualne działania sprawy</h3>
        <ul v-if="caseActions.length" class="case-actions">
          <li v-for="action in caseActions" :key="text(action.id)" :data-action-id="text(action.id)">
            {{ text(action.action) }}
          </li>
        </ul>
        <p v-else>Brak aktualnych działań sprawy w tym eksporcie.</p>
      </section>
      <section v-if="entity.kind === 'case'" class="case-questions"><h2>Pytania, kandydatury i powiązane obiekty</h2><p v-if="!caseQuestions.length">Brak pytań dopuszczonych do tego eksportu.</p><EntityLink v-for="question in caseQuestions.slice(0, 8)" :key="text(question.id)" kind="question" :id="text(question.id)" :label="caseQuestionLabel(question)" :status="text(question.workflowStage)" /><details v-if="caseQuestions.length > 8"><summary>Pokaż pozostałe pytania ({{ caseQuestions.length - 8 }})</summary><EntityLink v-for="question in caseQuestions.slice(8)" :key="text(question.id)" kind="question" :id="text(question.id)" :label="caseQuestionLabel(question)" :status="text(question.workflowStage)" /></details><h3>Powiązane obiekty</h3><template v-for="target in caseTargets" :key="`${text(target.type)}:${text(target.id)}`"><EntityLink v-if="targetKind(target.type) && target.id" :kind="targetKind(target.type)!" :id="text(target.id)" :label="text(target.label)" :availability="index.resolve(targetKind(target.type)!, text(target.id)).availability" /></template><p>Brak linku oznacza brak dopuszczonej referencji, a nie dowód nieistnienia.</p></section>

      <section v-if="entity.kind === 'question'" class="question-overview"><header class="detail-section-heading"><p class="eyebrow">Pytanie badawcze</p><h2>Cel, stan i kryterium ukończenia</h2></header><p class="question-statement">{{ text(raw.question) }}</p><p><strong>Kryterium:</strong> {{ text(raw.completionCriterion) || "Brak kryterium w eksporcie." }}</p><dl><dt>Priorytet</dt><dd>{{ text(raw.priorityLabel || raw.priority) || "nieustalony" }}</dd><dt>Zakres</dt><dd>{{ questionTargets.length ? `${questionTargets.length} powiązanych obiektów` : "brak jawnego zakresu" }}</dd><dt>Stan pytania</dt><dd>{{ statusLabel(text(raw.answerStatus)) }}</dd><dt>Etap procesu</dt><dd>{{ statusLabel(text(raw.workflowStage)) }}</dd><template v-if="!questionHasAssessment"><dt>Postęp pracy</dt><dd>{{ text(record(raw.progress).score) || "0" }}% — nie jest to prawdopodobieństwo tezy</dd><dt>Blokery</dt><dd>{{ questionBlockers.join(' · ') || "brak zapisanych blokerów" }}</dd></template><dt>Koszt</dt><dd>{{ questionCost }}</dd><dt>Źródło pytania</dt><dd>{{ statusLabel(text(raw.origin)) }}</dd><dt>Fingerprint</dt><dd>{{ text(raw.fingerprint) || "unknown" }}</dd></dl></section>
      <section v-if="entity.kind === 'question' && questionHasAssessment" class="question-assessment" :data-disposition="questionAssessment?.disposition" :data-work-id="questionAssessment?.scopedWork.id">
        <header class="detail-section-heading"><p class="eyebrow">Utrwalona ocena kuratora</p><h2>Aktualna ocena pytania</h2></header>
        <template v-if="questionAssessment">
          <p class="question-assessment-disposition">{{ researchAssessmentDispositionLabels[questionAssessment.disposition] }}</p>
          <dl>
            <dt>Brakujący dowód</dt><dd>{{ questionAssessment.missingEvidence }}</dd>
            <dt>Następny krok</dt><dd>{{ questionAssessment.nextAction }}</dd>
            <dt>Warunek powrotu</dt><dd>{{ questionAssessment.returnCondition }}</dd>
            <dt>Typ pracy w zakresie</dt><dd>{{ statusLabel(questionAssessment.scopedWork.itemType) }}</dd>
            <dt>Stan pracy</dt><dd>{{ statusLabel(questionAssessment.scopedWork.status) }}</dd>
          </dl>
          <details class="question-assessment-provenance"><summary>Pochodzenie oceny</summary><dl>
            <dt>Praca</dt><dd>{{ questionAssessment.scopedWork.id }}</dd>
            <dt>Zakres pracy</dt><dd>{{ questionAssessment.scopedWork.scopeId }}</dd>
            <dt>Praca nadrzędna</dt><dd>{{ questionAssessment.scopedWork.parentWorkItemId || 'brak' }}</dd>
            <dt>Zapis badawczy</dt><dd>{{ questionAssessment.source.logEntryId }}</dd>
            <dt>Wersja oceny</dt><dd>{{ questionAssessment.source.assessmentVersion }}</dd>
            <dt>Data oceny</dt><dd>{{ questionAssessment.source.assessedAt }}</dd>
            <dt>SHA-256 zapisu</dt><dd><code>{{ questionAssessment.source.summarySha256 }}</code></dd>
          </dl></details>
        </template>
        <p v-else class="question-assessment-unavailable">Utrwalona ocena jest niedostępna w tym eksporcie.</p>
      </section>
      <section v-if="entity.kind === 'question'"><h2>Cele i dowody</h2><template v-for="target in questionTargets" :key="text(target.id || target.label)"><EntityLink v-if="targetKind(target.type) && target.id" :kind="targetKind(target.type)!" :id="text(target.id)" :label="text(target.label)" /><p v-else>{{ text(target.label) }} · referencja niedostępna</p></template><p v-if="!questionHasAssessment"><strong>Następny krok:</strong> {{ text(raw.nextAction) || "unknown" }}</p></section>
      <section v-if="entity.kind === 'question'" class="research-execution-flow"><header class="detail-section-heading"><p class="eyebrow">Możliwości wykonania</p><h2>Dostępność kwerend i przebiegów</h2></header><ol class="execution-steps"><li class="is-ready"><strong>1. Pytanie i zakres</strong><span>deterministyczny input</span></li><li :class="{ 'is-ready': questionArchiveQueries.some((query) => query.executable) }"><strong>2. Kwerenda read-only</strong><span>{{ questionArchiveQueries.filter((query) => query.executable).length }} gotowych</span></li><li :class="{ 'is-ready': questionActiveBatches.length }"><strong>3. Agent-first review</strong><span>{{ questionActiveBatches.length ? `${questionActiveBatches.length} aktywnych` : questionBatches.length ? `${questionBatches.length} w historii` : 'jeszcze bez batcha' }}</span></li><li><strong>4. Decyzja kuratora</strong><span>{{ questionHasAssessment ? "utrwalona ocena powyżej" : "zawsze osobna bramka" }}</span></li></ol><p class="question-execution-gate"><strong>Możliwości wykonania kwerend:</strong> {{ text(questionExecution.activeGate) || "Brak planu wykonawczego." }}</p><p class="method-note">Wyszukanie, pobranie, writeback i decyzja identity nie uruchamiają się automatycznie.</p></section>
      <section v-if="entity.kind === 'question'"><header class="detail-section-heading"><p class="eyebrow">Adaptery archiwów</p><h2>Proponowane kwerendy</h2></header><article v-for="query in questionArchiveQueries" :key="text(query.id)" class="archive-query-card" :class="{ 'is-ready': query.executable }"><div><strong>{{ text(query.provider) }}</strong><span class="entity-status" :data-status="text(query.status)">{{ statusLabel(text(query.status)) }}</span></div><p>{{ archiveQuerySummary(query) }}</p><small>{{ text(query.gate || query.warning) }}</small><code v-if="query.executable">./genea question-archive-plan {{ entity.id }} --provider {{ text(query.provider) }} --output /tmp/{{ entity.id }}-{{ text(query.provider) }}.json</code></article></section>
      <section v-if="entity.kind === 'question' && questionBatches.length"><header class="detail-section-heading"><p class="eyebrow">Agent-first</p><h2>Powiązane batche</h2></header><article v-for="batch in questionBatches" :key="text(batch.id)" class="execution-batch-card"><div><strong>{{ text(batch.batchKey) }}</strong><span class="entity-status">{{ statusLabel(text(batch.status)) }}</span></div><p>{{ text(batch.stoppingRule) }}</p><small>Fingerprint: {{ text(batch.inputFingerprint).slice(0, 12) }}…</small></article></section>

      <section v-if="entity.kind === 'volume' && volume"><h2>Struktura i review</h2><dl><dt>Jednostki</dt><dd>{{ volume.unitCount ?? "unknown" }}</dd><dt>Regiony</dt><dd>{{ volume.regionCount ?? "unknown" }}</dd><dt>Przejrzane</dt><dd>{{ volume.reviewedCount ?? "unknown" }}</dd><dt>Ostrzeżenie</dt><dd>{{ volume.warning || "brak" }}</dd></dl><VolumeCandidateList :volume="volume" /></section>
      <section v-if="entity.kind === 'volume' && volume"><h2>Przebiegi OCR / HTR / AI</h2><EntityLink v-for="item in volume.runs || []" :key="item.id" kind="run" :id="item.id" :label="`${item.backend} ${item.model || ''}`" :status="item.status" /><p v-if="!volume.runs?.length">Brak zachowanej telemetrii przebiegów; koszt i jakość są unknown.</p></section>

      <section v-if="entity.kind === 'run' && run"><h2>Konfiguracja i provenance</h2><dl><dt>Backend</dt><dd>{{ run.backend }} {{ run.backendVersion }}</dd><dt>Model</dt><dd>{{ run.model || "unknown" }}</dd><dt>Hash konfiguracji</dt><dd>{{ run.configHash || "unknown" }}</dd><dt>Status</dt><dd>{{ run.status }}</dd></dl></section>
      <section v-if="entity.kind === 'run' && run"><h2>Telemetria</h2><dl><dt>Czas</dt><dd>{{ run.durationMs?.value ?? "unknown" }} · {{ run.durationMs?.provenance || "unknown" }}</dd><dt>Tokeny</dt><dd>{{ run.tokens?.value ?? "unknown" }} · {{ run.tokens?.provenance || "unknown" }}</dd><dt>Obrazy</dt><dd>{{ run.images?.value ?? "unknown" }} · {{ run.images?.provenance || "unknown" }}</dd><dt>Koszt</dt><dd>{{ run.cost?.value ?? "unknown" }} · {{ run.cost?.provenance || "unknown" }}</dd></dl><p>Brak wartości nie jest rekonstruowany z opisu ani liczby plików.</p></section>
    </div>
  </article>
</template>
