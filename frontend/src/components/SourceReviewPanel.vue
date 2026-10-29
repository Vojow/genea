<script setup lang="ts">
import { computed } from "vue";

import Badge from "./Badge.vue";
import ReadingVerificationCard from "./ReadingVerificationCard.vue";
import type {
  PresentationSource,
  SourceReadingComparison,
  SourceReadingDifference,
} from "../types/presentation";
import { reviewBadgeKind, sourceReviewFieldStatus, sourceReviewSummary } from "../../core/review-status.mjs";

const props = defineProps<{ source: PresentationSource }>();
const reviewSummary = computed(() => sourceReviewSummary(props.source));
function displayScalar(value: unknown): string | null {
  if (typeof value === "string") return value;
  return typeof value === "number" && Number.isFinite(value) ? String(value) : null;
}
const alternativeDispositionLabels: Record<string, string> = {
  rejected_as_literal: "odrzucony odczyt literalny",
  rejected_as_preferred_literal: "odrzucony jako preferowany odczyt literalny",
  retained_editorial_expansion_only: "wyłącznie rozwinięcie redakcyjne",
  unresolved: "nierozstrzygnięty",
  unresolved_disfavored: "nierozstrzygnięty, słabszy",
  unresolved_literal_spelling: "nierozstrzygnięta pisownia literalna",
  unresolved_unaccepted: "nieprzyjęty, nierozstrzygnięty",
  retained_disfavored: "zachowany, słabszy",
  retained_blindA_notation: "zapis pierwszego odczytu ślepego",
  retained_blindB_notation: "zapis drugiego odczytu ślepego",
};
function fieldAlternatives(field: Record<string, unknown> | null) {
  const literals = [typeof field?.alternative === "string" ? field.alternative : "",
    ...(Array.isArray(field?.alternatives) ? field.alternatives.map(displayScalar) : [])]
    .filter((value): value is string => value !== null && value !== "");
  const dispositions = Array.isArray(field?.alternativeDispositions)
    ? field.alternativeDispositions.flatMap((entry: unknown) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
      const record = entry as Record<string, unknown>;
      const value = displayScalar(record.value);
      if (value === null || value === "") return [];
      const status = typeof record.status === "string" && Object.hasOwn(alternativeDispositionLabels, record.status)
        ? record.status : null;
      return [{ value, status }];
    }) : [];
  return [...new Set([...literals, ...dispositions.map((entry) => entry.value)])].map((value) => {
    const matches = dispositions.filter((entry) => entry.value === value);
    const statuses = new Set(matches.map((entry) => entry.status));
    const status = statuses.size === 1 ? matches[0]?.status ?? null : null;
    return { value, status, label: status ? alternativeDispositionLabels[status] : "stan alternatywy nieustalony" };
  });
}
function fieldScalarAlias(field: Record<string, unknown> | null, currentKeys: string[], legacyKey: string) {
  if (!field) return { text: "", ambiguous: false };
  const current = currentKeys.filter((key) => Object.hasOwn(field, key));
  const present = [...current, ...(Object.hasOwn(field, legacyKey) ? [legacyKey] : [])];
  const values = present.map((key) => displayScalar(field[key]));
  const malformed = present.some((key) => field[key] != null && displayScalar(field[key]) === null);
  const conflicting = present.length > 1 && (values.some((value) => value === null) || new Set(values).size > 1);
  if (malformed || conflicting) return { text: "", ambiguous: true };
  const selected = current[0] || (Object.hasOwn(field, legacyKey) ? legacyKey : null);
  return { text: selected ? displayScalar(field[selected]) ?? "" : "", ambiguous: false };
}
const reviewFields = computed(() => {
  const fields: unknown = props.source.reviewWorkflow?.fields;
  if (!Array.isArray(fields)) return [];
  return fields.map((value: unknown, index: number) => {
    const field = value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : null;
    const literal = (key: string): string => typeof field?.[key] === "string" ? field[key] as string : "";
    const path = fieldScalarAlias(field, ["fieldPath", "field"], "path");
    const primaryValue = fieldScalarAlias(field, ["value", "preferredValue"], "rawValue");
    const explicitNull = field && ["value", "preferredValue", "rawValue"].some((key) =>
      Object.hasOwn(field, key) && field[key] === null);
    const absenceCode = path.ambiguous || primaryValue.ambiguous ? ""
      : field?.valueKind === "absence_observation" && primaryValue.text === "NotStated" ? "NotStated"
        : field?.certainty === "NotRecorded" && explicitNull ? "NotRecorded" : "";
    return { position: index + 1, path: path.text,
      value: absenceCode === "NotRecorded" ? "Nie zapisano w akcie" : absenceCode ? "Nie podano w akcie" : primaryValue.text,
      absenceCode, absenceRationale: absenceCode ? literal("rationale") : "",
      alternatives: fieldAlternatives(field), rationale: literal("rationale"),
      contextOnly: literal("context_only"), risk: literal("risk"),
      certainty: literal("certainty"), status: path.ambiguous || primaryValue.ambiguous ? null : sourceReviewFieldStatus(field) };
  });
});
const processCodes = computed(() => reviewFields.value.filter((field) => field.absenceCode));
function reviewCount(value: unknown): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? String(value) : "nieustalone";
}
function fieldBadgeKind(status: string | null): string {
  return reviewBadgeKind(status === "agent_consensus" || status === "agent_adjudicated" ? "agent_approved" : status);
}

const comparisonReviews = computed(() => (props.source.comparisons || []).flatMap((comparison) =>
  comparison.differences
    .filter((difference) => difference.literalReading || difference.comparisonEvidence || difference.hypothesis || difference.identityDecision)
    .map((difference) => ({ comparison, difference })),
));
const latestPipelineRun = computed(() => props.source.readingPipelineRuns?.[0] || null);
const verificationArtifactUrl = computed(() => {
  const artifactId = latestPipelineRun.value?.verificationCard?.fields[0]?.backing.artifactId
    || latestPipelineRun.value?.artifactId;
  const artifact = props.source.artifacts?.find((candidate) =>
    candidate.id === artifactId || (artifactId != null && candidate.aliasIds?.includes(artifactId)),
  );
  return artifact?.viewer === "image" ? artifact.url : null;
});

function pipelineStatusLabel(status: string): string {
  return status === "ready_for_curator" ? "gotowe do kontroli kuratora" : "wymaga dodatkowego odczytu";
}

function escalationReasonLabel(reason: string): string {
  return {
    missing_required_field: "brak wymaganego pola",
    low_confidence: "niska pewność odczytu",
    ambiguous_reading: "odczyt niejednoznaczny",
    illegible_reading: "fragment nieczytelny lub uszkodzony",
    missing_source_quote: "brak cytatu źródłowego",
    missing_layer: "brak warstwy odczytu",
    missing_locator: "brak pełnego locatora",
    artifact_hash_mismatch: "niezgodny SHA-256 artefaktu",
    independent_reading_disagreement: "niezależne odczyty są sprzeczne",
    additional_name_disagreement: "dodatkowe imię występuje tylko w części odczytów",
    expected_role_missing: "brak oczekiwanej roli w ekstrakcie",
    image_region_obstructed: "fragment obrazu jest zasłonięty lub uszkodzony",
    literal_normalization_divergence: "normalizacja wykracza poza zapis literalny",
    material_alternative_present: "istnieje materialna alternatywa odczytu",
  }[reason] || reason.replaceAll("_", " ");
}

function pipelineRoleLabel(role: string): string {
  return {
    blind_paleographer: "niezależny paleograf",
    contextual_reader: "czytelnik kontekstowy",
    agent_adjudicator: "adjudykator",
    source_auditor: "audytor źródła",
    human_curator: "kurator",
  }[role] || role.replaceAll("_", " ");
}

function coverageDimensionLabel(value: string): string {
  return {
    inventory: "Inwentarz",
    index: "Indeks",
    content_review: "Przegląd treści",
    extraction: "Ekstrakcja",
    transcription: "Transkrypcja",
    review: "Kontrola",
  }[value] || value.replaceAll("_", " ");
}

function coverageLevelLabel(value: string): string {
  return {
    manifest: "manifest",
    minimal: "minimalny",
    target_search: "kwerenda celowa",
    target_only: "tylko cel",
    fan: "pełny FAN",
    extract: "ekstrakt",
    full_literal: "pełna literalna",
    agent_reviewed: "kontrola agentowa",
    human_reviewed: "kontrola człowieka",
    full_act: "pełny akt",
    selected_fields: "wybrane pola",
  }[value] || value.replaceAll("_", " ");
}

function recordTypeLabel(value?: string | null): string {
  return {
    birth: "urodzenie",
    baptism: "chrzest",
    marriage: "małżeństwo",
    death: "zgon",
    burial: "pochówek",
  }[value || ""] || value?.replaceAll("_", " ") || "";
}

function coverageCount(completed?: number | null, total?: number | null): string {
  if (completed == null && total == null) return "zakres liczbowy nieustalony";
  return `${completed ?? "nieustalone"}/${total ?? "nieustalone"}`;
}

function comparisonStatus(comparison: SourceReadingComparison): string {
  return comparison.status === "reviewed" ? "po recenzji" : "wersja robocza";
}

function comparisonFieldLabel(difference: SourceReadingDifference): string {
  const labels: Record<string, string> = { "mother.surname": "nazwisko matki" };
  if (difference.field && labels[difference.field]) return labels[difference.field];
  return difference.field?.replaceAll("_", " ").replaceAll(".", " · ") || "pole sporne";
}

function reviewFieldLabel(field?: string): string {
  const labels: Record<string, string> = {
    father_name: "imię i nazwisko ojca",
    father_age: "wiek ojca",
    mother_name: "imię i nazwisko matki",
    mother_age: "wiek matki",
    act_time: "godzina aktu",
    witness_1: "pierwszy świadek",
    witness_2: "drugi świadek",
    witness_2_descriptor: "opis drugiego świadka",
    godfather: "chrzestny",
    godmother: "chrzestna",
    "groom.name": "pan młody · imię i nazwisko",
    "groom.parents": "pan młody · rodzice",
    "groom.age": "pan młody · wiek",
    "groom.church_record": "pan młody · metryka kościelna",
    "groom.residence_status": "pan młody · zamieszkanie i status",
    "bride.name": "panna młoda · imię i nazwisko",
    "bride.parents": "panna młoda · rodzice",
    "bride.age_status": "panna młoda · wiek i stan",
    "bride.church_record": "panna młoda · metryka kościelna",
    "bride.residence": "panna młoda · miejsce zamieszkania",
    witnesses: "świadkowie",
    "act.number": "numer aktu",
    "act.placeHeading": "miejscowość w nagłówku",
    "registration.place": "miejsce sporządzenia aktu",
    "registration.dateLiteral": "data sporządzenia aktu · zapis źródłowy",
    "registration.timeLiteral": "godzina sporządzenia aktu · zapis źródłowy",
    "father.nameLiteral": "ojciec · imię i nazwisko",
    "father.ageLiteral": "ojciec · wiek",
    "father.occupationLiteral": "ojciec · zawód",
    "father.residenceLiteral": "ojciec · miejsce zamieszkania",
    "witness1.nameLiteral": "pierwszy świadek · imię i nazwisko",
    "witness1.ageLiteral": "pierwszy świadek · wiek",
    "witness1.occupationLiteral": "pierwszy świadek · zawód",
    "witness1.residenceLiteral": "pierwszy świadek · miejsce zamieszkania",
    "witness2.nameLiteral": "drugi świadek · imię i nazwisko",
    "witness2.ageLiteral": "drugi świadek · wiek",
    "witness2.occupationLiteral": "drugi świadek · zawód",
    "witness2.residenceLiteral": "drugi świadek · miejsce zamieszkania",
    "birth.dateLiteral": "urodzenie · data w źródle",
    "birth.timeLiteral": "urodzenie · godzina w źródle",
    "birth.placeLiteral": "urodzenie · miejsce w źródle",
    "child.sexLiteral": "dziecko · płeć",
    "mother.givenNameLiteral": "matka · imię",
    "mother.birthFamilyNameLiteral": "matka · nazwisko rodowe w źródle",
    "mother.ageLiteral": "matka · wiek",
    "mother.marriedSurnameLiteral": "matka · nazwisko po ślubie w źródle",
    "mother.residenceLiteral": "matka · miejsce zamieszkania",
    "child.givenNameLiteral": "dziecko · imię",
    "child.surnameLiteral": "dziecko · nazwisko w źródle",
    "baptism.dateLiteral": "chrzest · data w źródle",
    "godfather.nameLiteral": "chrzestny · imię i nazwisko",
    "godmother.nameLiteral": "chrzestna · imię i nazwisko",
    "act.readingAndLiteracyLiteral": "odczytanie aktu i piśmienność · zapis źródłowy",
    "officiant.officeLiteral": "sporządzający akt · funkcja",
    "officiant.signatureLiteral": "sporządzający akt · podpis",
    actNumber: "numer aktu",
    marginPrefix: "oznaczenie numeru na marginesie",
    registrationPlace: "miejsce sporządzenia aktu",
    registrationDate: "data sporządzenia aktu · zapis źródłowy",
    registrationTime: "godzina sporządzenia aktu · zapis źródłowy",
    witness1GivenName: "pierwszy świadek · imię",
    witness1Surname: "pierwszy świadek · nazwisko",
    witness1Age: "pierwszy świadek · wiek",
    witness2GivenName: "drugi świadek · imię",
    witness2Surname: "drugi świadek · nazwisko",
    witness2Age: "drugi świadek · wiek",
    witnessesOccupation: "świadkowie · zawód",
    witnessesPlace: "świadkowie · miejscowość w źródle",
    deathDate: "zgon · data w źródle",
    deathTime: "zgon · godzina w źródle",
    deathPlace: "zgon · miejsce w źródle",
    deceasedName: "zmarły · imię i nazwisko",
    deceasedAge: "zmarły · wiek",
    deceasedBirthPlace: "zmarły · miejsce urodzenia",
    deceasedResidence: "zmarły · miejsce zamieszkania",
    deceasedOccupation: "zmarły · zawód",
    fatherGivenName: "ojciec · imię",
    fatherSurname: "ojciec · nazwisko w źródle",
    motherGivenName: "matka · imię",
    motherMaidenFamily: "matka · nazwisko rodowe w źródle",
    parentRelationLiteral: "rodzice · zapis relacji w źródle",
    readingAndSigning: "odczytanie i podpisanie aktu · zapis źródłowy",
    registrarRole: "sporządzający akt · funkcja",
    signature: "sporządzający akt · podpis",
  };
  return field && labels[field] ? labels[field] : field?.replaceAll("_", " ").replaceAll(".", " · ") || "pole nieopisane";
}

function reviewFieldStatus(status: string | null): string {
  return {
    agent_single_pass: "jeden odczyt agentowy",
    agent_consensus: "zgoda agentów",
    agent_adjudicated: "rozstrzygnięte agentowo",
    human_review_required: "wymaga Twojej kontroli",
    human_reviewed: "sprawdzone przez Ciebie",
    agent_reviewed: "sprawdzone przez agentów",
    unreviewed: "bez kontroli",
  }[status || ""] || status || "stan kontroli nieustalony";
}
</script>

<template>
  <ReadingVerificationCard
    v-if="latestPipelineRun?.verificationCard"
    :card="latestPipelineRun.verificationCard"
    :artifact-url="verificationArtifactUrl"
  />

  <section v-if="latestPipelineRun" class="reading-pipeline-panel source-review-section" aria-label="Automatyczny pipeline odczytu">
    <div class="pane-heading">
      <div><p class="eyebrow">Pierwsze przejście AI</p><h2>Kompletność i koszt odczytu</h2></div>
      <Badge :kind="latestPipelineRun.status === 'ready_for_curator' ? 'is-good' : 'is-warning'">{{ pipelineStatusLabel(latestPipelineRun.status) }}</Badge>
    </div>
    <p class="review-rationale">{{ latestPipelineRun.provider }} · {{ latestPipelineRun.model }}. To propozycja odczytu, nie fakt genealogiczny ani decyzja tożsamości.</p>
    <div class="review-workflow-stats">
      <span><strong>{{ Math.round(latestPipelineRun.completeness * 100) }}%</strong> pól wymaganych</span>
      <span><strong>{{ latestPipelineRun.summary.fieldCount }}</strong> pól odczytanych</span>
      <span><strong>{{ latestPipelineRun.summary.independentReadingCount || 0 }}</strong> niezależne odczyty</span>
      <span><strong>{{ latestPipelineRun.summary.disagreementCount || 0 }}</strong> rozbieżności</span>
      <span><strong>{{ latestPipelineRun.summary.escalationCount }}</strong> eskalacji</span>
    </div>
    <div v-if="latestPipelineRun.escalations.length" class="review-field-list">
      <article v-for="(issue, index) in latestPipelineRun.escalations" :key="issue.id || `${issue.reasonCode}:${index}`" class="review-field" data-review-state="human_review_required">
        <header><strong>{{ reviewFieldLabel(issue.fieldPath || undefined) }}</strong><Badge kind="is-warning">P{{ issue.priority }}</Badge></header>
        <p>{{ escalationReasonLabel(issue.reasonCode) }}</p>
        <small>Następny krok: {{ pipelineRoleLabel(issue.recommendedRole || "human_curator") }}</small>
      </article>
    </div>
    <details class="source-technical-note">
      <summary>Metryki przebiegu</summary>
      <p>Pipeline {{ latestPipelineRun.pipelineVersion }} · język {{ latestPipelineRun.sourceLanguage }} · command {{ latestPipelineRun.commandId }}</p>
      <pre>{{ JSON.stringify(latestPipelineRun.metrics || {}, null, 2) }}</pre>
    </details>
  </section>

  <section v-if="comparisonReviews.length" class="comparison-panel source-review-section" aria-label="Porównania odczytów">
    <div class="pane-heading"><div><p class="eyebrow">Kontrola odczytu</p><h2>Co naprawdę wynika z porównania</h2></div></div>
    <article v-for="review in comparisonReviews" :key="`${review.comparison.id}-${review.difference.field}`" class="comparison-card">
      <header>
        <strong>{{ comparisonFieldLabel(review.difference) }}</strong>
        <Badge :kind="review.comparison.status === 'reviewed' ? 'is-good' : 'is-warning'">{{ comparisonStatus(review.comparison) }}</Badge>
      </header>
      <dl class="comparison-grid">
        <div><dt>Odczyt literalny</dt><dd>{{ review.difference.literalReading }}</dd></div>
        <div><dt>Dowód porównawczy</dt><dd>{{ review.difference.comparisonEvidence }}</dd></div>
        <div><dt>Hipoteza</dt><dd>{{ review.difference.hypothesis }}</dd></div>
        <div><dt>Decyzja tożsamości</dt><dd>{{ review.difference.identityDecision }}</dd></div>
      </dl>
      <details>
        <summary>Porównaj odczyt ślepy i kontekstowy</summary>
        <p><b>Odczyt ślepy:</b> {{ review.difference.blind || "brak" }}</p>
        <p><b>Odczyt kontekstowy:</b> {{ review.difference.contextual || "brak" }}</p>
        <p v-if="review.difference.note">{{ review.difference.note }}</p>
      </details>
    </article>
  </section>

  <section v-if="source.reviewWorkflow" class="review-workflow-panel source-review-section" aria-label="Kontrola agentowa i kolejka kontroli człowieka">
    <div class="pane-heading">
      <div><p class="eyebrow">Kontrola opracowania</p><h2>Co jest gotowe, a co czeka na Ciebie</h2></div>
      <Badge :kind="reviewBadgeKind(reviewSummary?.state)">{{ reviewSummary?.label }}</Badge>
    </div>
    <p class="review-rationale"><template v-if="reviewSummary?.fieldReviewState === 'unknown'">Stan kontroli pól nieustalony. </template><template v-else-if="reviewSummary?.fieldReviewState === 'partial'">Zestawienie obejmuje znany podzbiór pól; {{ reviewCount(reviewSummary.unmappedFieldCount) }} pól ma nieustalony stan. </template>{{ reviewCount(reviewSummary?.agentApprovedFieldCount) }} pól ma rozstrzygnięcie agentowe, a {{ reviewCount(reviewSummary?.humanReviewRequiredFieldCount) }} wymaga jeszcze Twojej kontroli. To stan opracowania odczytu, nie automatyczna decyzja o faktach ani tożsamości.</p>
    <div class="review-workflow-stats">
      <span><strong>{{ reviewCount(source.reviewWorkflow.summary.blindInputCount) }}</strong> niezależne odczyty ślepe</span>
      <span><strong>{{ reviewCount(reviewSummary?.fieldCount) }}</strong> pól w opracowaniu</span>
      <span><strong>{{ reviewCount(reviewSummary?.agentApprovedFieldCount) }}</strong> pól rozstrzygniętych agentowo</span>
      <span><strong>{{ reviewCount(reviewSummary?.singlePassFieldCount) }}</strong> pól z jednym odczytem agentowym</span>
      <span><strong>{{ reviewCount(reviewSummary?.humanReviewRequiredFieldCount) }}</strong> pól dla Ciebie</span>
      <span v-if="reviewSummary?.fieldReviewState !== 'known'"><strong>{{ reviewCount(reviewSummary?.unmappedFieldCount) }}</strong> pól o nieustalonym stanie</span>
    </div>
    <div class="review-field-list">
      <article v-for="field in reviewFields" :key="field.position" class="review-field" :data-review-position="field.position" :data-review-state="field.status || 'unknown'">
        <header><strong>{{ field.path ? reviewFieldLabel(field.path) : `Pole ${field.position} · nieopisane` }}</strong><Badge :kind="fieldBadgeKind(field.status)">{{ reviewFieldStatus(field.status) }}</Badge></header>
        <p>{{ field.value || "wartość nieustalona" }}</p>
        <small v-for="alternative in field.alternatives" :key="alternative.value" :data-alternative-disposition="alternative.status || 'unknown'">Alternatywa: {{ alternative.value }} · {{ alternative.label }}</small>
        <p v-if="field.rationale" class="review-field-rationale">Uzasadnienie: {{ field.rationale }}</p>
        <small v-if="field.contextOnly">Tylko kontekst: {{ field.contextOnly }}</small>
        <small v-if="field.risk">Ryzyko {{ field.risk }}<template v-if="field.certainty"> · {{ field.certainty }}</template></small>
      </article>
    </div>
    <details v-if="source.reviewWorkflow.rationale || processCodes.length" class="source-technical-note">
      <summary>Oryginalna notatka procesu</summary>
      <p v-if="source.reviewWorkflow.rationale">{{ source.reviewWorkflow.rationale }}</p>
      <p v-for="field in processCodes" :key="field.position">{{ reviewFieldLabel(field.path) }} · oryginalny kod obserwacji braku: {{ field.absenceCode }}<template v-if="field.absenceRationale"> · {{ field.absenceRationale }}</template></p>
    </details>
  </section>

  <section v-if="source.coverage?.length" class="source-coverage-panel source-review-section" aria-label="Pokrycie opracowania">
    <div class="pane-heading"><div><p class="eyebrow">Kompletność pracy</p><h2>Pokrycie opracowania</h2></div></div>
    <div class="source-coverage-list">
      <article v-for="assessment in source.coverage" :key="assessment.id" class="source-coverage-card" :data-coverage-status="assessment.status">
        <header>
          <strong>{{ coverageDimensionLabel(assessment.dimension) }}</strong>
          <Badge :kind="assessment.status === 'complete' ? 'is-good' : assessment.status === 'blocked' ? 'is-conflict' : 'is-warning'">{{ coverageCount(assessment.completed, assessment.total) }}</Badge>
        </header>
        <p>{{ coverageLevelLabel(assessment.level) }}<template v-if="assessment.recordType"> · {{ recordTypeLabel(assessment.recordType) }}</template></p>
        <small>{{ assessment.systematic ? "przegląd systematyczny" : "przegląd częściowy" }} · {{ reviewFieldStatus(assessment.qaStatus) }}</small>
        <details class="source-technical-note">
          <summary>Zakres i oryginalna notatka techniczna</summary>
          <p>{{ assessment.scope }}</p>
          <p>{{ assessment.rationale }}</p>
        </details>
      </article>
    </div>
  </section>
</template>
