<script setup lang="ts">
import { computed, ref } from "vue";

import Badge from "./Badge.vue";
import type {
  ReadingFieldComparisonStatus,
  SourceReadingVerificationCard,
  SourceReadingVerificationField,
} from "../types/presentation";

const props = defineProps<{
  card: SourceReadingVerificationCard;
  artifactUrl?: string | null;
}>();

type FieldFilter = "all" | "agreed" | "disputed" | "review";

const filter = ref<FieldFilter>("all");
const presentationMode = ref(false);
const imageSize = ref<{ width: number; height: number } | null>(null);
const imageFailed = ref(false);

const filters: Array<{ id: FieldFilter; label: string }> = [
  { id: "all", label: "Wszystkie" },
  { id: "agreed", label: "Zgodne" },
  { id: "disputed", label: "Sporne i niepełne" },
  { id: "review", label: "Do kontroli" },
];

const fields = computed(() => props.card.fields.filter((field) => {
  if (filter.value === "agreed") return field.comparison.status === "agreed";
  if (filter.value === "disputed") return ["disputed", "incomplete"].includes(field.comparison.status);
  if (filter.value === "review") return field.workflow.status === "human_review_required";
  return true;
}));

function fieldLabel(path: string): string {
  const labels: Record<string, string> = {
    "event.date": "Data zdarzenia",
    "event.place": "Miejsce zdarzenia",
    "groom.name": "Pan młody · imię i nazwisko",
    "groom.age": "Pan młody · wiek",
    "groom.parents": "Pan młody · rodzice",
    "groom.father.name": "Pan młody · ojciec",
    "groom.mother.name": "Pan młody · matka",
    "bride.name": "Panna młoda · imię i nazwisko",
    "bride.age": "Panna młoda · wiek",
    "bride.parents": "Panna młoda · rodzice",
    "bride.father.name": "Panna młoda · ojciec",
    "bride.mother.name": "Panna młoda · matka",
    witnesses: "Świadkowie",
    "marriage.bannsConsent": "Zapowiedzi i zgody",
  };
  return labels[path] || path.replaceAll(".", " · ").replaceAll("_", " ");
}

function comparisonLabel(status: ReadingFieldComparisonStatus): string {
  return {
    agreed: "odczyty zgodne",
    disputed: "odczyty sporne",
    incomplete: "porównanie niepełne",
    not_compared: "nie porównano",
  }[status];
}

function comparisonBadge(status: ReadingFieldComparisonStatus): string {
  if (status === "agreed") return "is-good";
  if (status === "disputed") return "is-conflict";
  return "is-warning";
}

function workflowLabel(status: string): string {
  return {
    not_reviewed: "bez recenzji",
    machine_proposal: "propozycja AI",
    agent_consensus: "zgoda agentów",
    agent_adjudicated: "rozstrzygnięte agentowo",
    human_review_required: "wymaga Twojej kontroli",
    human_reviewed: "sprawdzone przez Ciebie",
    missing: "brak pola",
  }[status] || status.replaceAll("_", " ");
}

function workflowBadge(status: string): string {
  if (status === "human_reviewed") return "is-good";
  if (status === "human_review_required" || status === "missing") return "is-warning";
  return "is-neutral";
}

function cropStyle(field: SourceReadingVerificationField): Record<string, string> | undefined {
  const bbox = field.backing.bbox;
  const size = imageSize.value;
  if (!bbox || !size || bbox.width <= 0 || bbox.height <= 0) return undefined;
  return {
    width: `${(size.width / bbox.width) * 100}%`,
    height: `${(size.height / bbox.height) * 100}%`,
    left: `${-(bbox.x / bbox.width) * 100}%`,
    top: `${-(bbox.y / bbox.height) * 100}%`,
  };
}

function cropAspect(field: SourceReadingVerificationField): string | undefined {
  const bbox = field.backing.bbox;
  return bbox && bbox.width > 0 && bbox.height > 0 ? `${bbox.width} / ${bbox.height}` : undefined;
}

function onImageLoad(event: Event): void {
  const image = event.currentTarget as HTMLImageElement;
  imageSize.value = { width: image.naturalWidth, height: image.naturalHeight };
  imageFailed.value = false;
}

function onImageError(): void {
  imageFailed.value = true;
  imageSize.value = null;
}
</script>

<template>
  <section class="reading-verification-card source-review-section" :class="{ 'is-presentation': presentationMode }" aria-labelledby="verification-card-title">
    <img v-if="artifactUrl" class="reading-verification-image-loader" :src="artifactUrl" alt="" aria-hidden="true" @load="onImageLoad" @error="onImageError" />
    <header class="reading-verification-heading">
      <div>
        <p class="eyebrow">Karta weryfikacji aktu</p>
        <h2 id="verification-card-title">Pola odczytane z obrazu</h2>
        <p>Zgodność modeli i status recenzji są pokazane osobno. Zgodne pole nadal może wymagać kontroli człowieka.</p>
      </div>
      <button type="button" class="reading-presentation-toggle" :aria-pressed="presentationMode" @click="presentationMode = !presentationMode">
        {{ presentationMode ? "Pokaż warsztat" : "Widok prezentacyjny" }}
      </button>
    </header>

    <div class="reading-verification-summary" aria-label="Podsumowanie pól">
      <span><strong>{{ card.summary.fieldCount }}</strong> wszystkich</span>
      <span><strong>{{ card.summary.agreedCount }}</strong> zgodnych</span>
      <span><strong>{{ card.summary.disputedCount + card.summary.incompleteCount }}</strong> spornych lub niepełnych</span>
      <span><strong>{{ card.summary.humanReviewRequiredCount }}</strong> do kontroli</span>
    </div>

    <div class="reading-verification-filters" role="group" aria-label="Filtr pól aktu">
      <button
        v-for="item in filters"
        :key="item.id"
        type="button"
        :class="{ 'is-active': filter === item.id }"
        :aria-pressed="filter === item.id"
        @click="filter = item.id"
      >{{ item.label }}</button>
    </div>

    <div class="reading-verification-fields">
      <article v-for="field in fields" :key="field.path" class="reading-verification-field" :data-comparison="field.comparison.status" :data-workflow="field.workflow.status">
        <div v-if="artifactUrl && !imageFailed && field.backing.bbox" class="reading-field-crop" :style="{ aspectRatio: cropAspect(field) }">
          <img :src="artifactUrl" :alt="`Wycinek pola: ${fieldLabel(field.path)}`" :style="cropStyle(field)" @error="onImageError" />
        </div>
        <div v-else class="reading-field-crop is-missing"><span>Brak bezpiecznego wycinka dla tego pola</span></div>

        <div class="reading-field-content">
          <header>
            <div><small>{{ field.path }}</small><h3>{{ fieldLabel(field.path) }}</h3></div>
            <div class="reading-field-badges">
              <Badge :kind="comparisonBadge(field.comparison.status)">{{ comparisonLabel(field.comparison.status) }}</Badge>
              <Badge :kind="workflowBadge(field.workflow.status)">{{ workflowLabel(field.workflow.status) }}</Badge>
            </div>
          </header>

          <p class="reading-field-value">{{ field.proposal.value || "brak odczytu literalnego" }}</p>
          <p v-if="field.proposal.normalizedValue && field.proposal.normalizedValue !== field.proposal.value" class="reading-field-literal"><strong>Interpretacja / forma polska:</strong> {{ field.proposal.normalizedValue }}</p>
          <p v-if="field.proposal.sourceQuote" class="reading-field-quote">„{{ field.proposal.sourceQuote }}”</p>

          <div v-if="field.blindReadings.length" class="reading-blind-values">
            <div v-for="(reading, index) in field.blindReadings" :key="`${field.path}:${reading.readingId}`">
              <span>Odczyt {{ index + 1 }}</span>
              <strong>{{ reading.value || "nie odczytano" }}</strong>
            </div>
          </div>

          <p v-if="field.proposal.materialAlternatives.length" class="reading-field-alternatives"><strong>Materialne alternatywy:</strong> {{ field.proposal.materialAlternatives.join(" · ") }}</p>
          <p v-else-if="field.proposal.alternatives.length" class="reading-field-alternatives"><strong>Alternatywy:</strong> {{ field.proposal.alternatives.join(" · ") }}</p>

          <details v-if="!presentationMode" class="reading-field-provenance">
            <summary>Pochodzenie i kontrola</summary>
            <p>{{ field.proposal.certainty || "pewność nieopisana" }}<template v-if="field.proposal.confidence != null"> · confidence {{ Math.round(field.proposal.confidence * 100) }}%</template></p>
            <p v-for="(issue, index) in field.issues" :key="issue.id || `${field.path}:${index}`">{{ issue.reasonCode.replaceAll("_", " ") }} · następny krok: {{ issue.recommendedRole?.replaceAll("_", " ") || "kontrola kuratorska" }}</p>
            <small>Źródło {{ field.backing.sourceItemId }} · artefakt {{ field.backing.artifactId }}<template v-if="field.backing.sourceSegmentId"> · segment {{ field.backing.sourceSegmentId }}</template></small>
          </details>
        </div>
      </article>
    </div>

    <p v-if="!fields.length" class="empty-state-card">Brak pól pasujących do wybranego filtra.</p>
  </section>
</template>
