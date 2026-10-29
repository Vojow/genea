<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { languageFamily, projectSourceReadings, readingLanguages, readingStatusLabel, readingTypeLabel } from "../models/source-reading-projector";
import type { PresentationSource, SourceArtifact, SourceReading, SourceSegment } from "../types/presentation";
import TranscriptContent from "./TranscriptContent.vue";

const props = withDefaults(defineProps<{
  source: PresentationSource;
  preferredLanguage?: string;
  selectedReadingId?: string | null;
  initialOriginal?: boolean;
}>(), {
  preferredLanguage: "",
  selectedReadingId: null,
  initialOriginal: true,
});

const emit = defineEmits<{ "update:selectedReadingId": [readingId: string | null] }>();
const showOriginal = ref(props.initialOriginal);
const localReadingId = ref<string | null>(props.selectedReadingId);
const selectedArtifactId = ref<string | null>(null);

const interfaceLanguage = computed(() => props.preferredLanguage
  || (typeof document !== "undefined" ? document.documentElement.lang : "")
  || (typeof navigator !== "undefined" ? navigator.language : "")
  || "pl");
const readingModel = computed(() => projectSourceReadings(props.source.readings, interfaceLanguage.value));
const selectedReading = computed(() => props.source.readings.find((reading) => reading.id === localReadingId.value)
  || readingModel.value.primary);
const selectedIsPrimary = computed(() => selectedReading.value?.id === readingModel.value.primary?.id);
const selectedArtifact = computed(() => {
  const artifacts = props.source.artifacts || [];
  return artifacts.find((artifact) => artifact.id === selectedArtifactId.value)
    || artifacts.find((artifact) => !artifact.derived)
    || artifacts[0]
    || null;
});
const sortedSegments = computed(() => [...(selectedReading.value?.segments || [])].sort((first, second) => {
  const rank = (segment: SourceSegment): number => readingModel.value.preferredLanguage === languageFamily(segment.language) ? 0 : 1;
  return rank(first) - rank(second) || first.sequence - second.sequence;
}));
const displayedSegments = computed(() => {
  if (!selectedIsPrimary.value || !readingModel.value.preferredLanguageMatched) return sortedSegments.value;
  return sortedSegments.value.filter((segment) => languageFamily(segment.language) === readingModel.value.preferredLanguage);
});
const supplementalLanguageSegments = computed(() => {
  if (!selectedIsPrimary.value || !readingModel.value.preferredLanguageMatched) return [];
  return sortedSegments.value.filter((segment) => languageFamily(segment.language) !== readingModel.value.preferredLanguage);
});

watch(() => props.selectedReadingId, (readingId) => {
  localReadingId.value = readingId;
});

watch(() => props.source.id, () => {
  showOriginal.value = props.initialOriginal;
  selectedArtifactId.value = props.source.artifacts?.find((artifact) => !artifact.derived)?.id
    || props.source.artifacts?.[0]?.id
    || null;
  selectReading(readingModel.value.primary);
}, { immediate: true });

watch(() => readingModel.value.primary?.id, (readingId) => {
  if (!localReadingId.value || !props.source.readings.some((reading) => reading.id === localReadingId.value)) {
    selectReading(readingModel.value.primary);
  }
});

function selectReading(reading: SourceReading | null): void {
  localReadingId.value = reading?.id || null;
  emit("update:selectedReadingId", localReadingId.value);
}

function artifactViewerTag(artifact: SourceArtifact): "img" | "iframe" {
  return artifact.viewer === "image" ? "img" : "iframe";
}

function versionLabel(reading: SourceReading): string {
  const languages = readingLanguages(reading).map((language) => ({ pl: "polski", ru: "rosyjski", la: "łacina", de: "niemiecki" })[language] || language);
  return [readingTypeLabel(reading.type), languages.join(" + "), `v${reading.version}`].filter(Boolean).join(" · ");
}
</script>

<template>
  <div class="source-document-reader">
    <div class="source-reader-toolbar">
      <div>
        <p class="eyebrow">Dokument i odczyt</p>
        <strong>{{ source.artifacts?.length ? "Porównuj tekst bez odrywania go od obrazu aktu" : "Odczyt bez lokalnego materiału do podglądu" }}</strong>
      </div>
      <label v-if="source.artifacts?.length" class="source-original-switch">
        <input v-model="showOriginal" type="checkbox" role="switch" aria-label="Pokaż oryginał dokumentu" />
        <span>{{ showOriginal ? "Ukryj oryginał" : "Pokaż oryginał" }}</span>
      </label>
    </div>

    <div class="source-reading-area" :class="{ 'has-original': showOriginal && selectedArtifact }">
      <section v-if="showOriginal && selectedArtifact" class="original-pane">
        <div class="pane-heading original-heading">
          <div>
            <h3>{{ selectedArtifact.derived ? "Materiał pochodny" : "Oryginał dokumentu" }}</h3>
            <small>{{ selectedArtifact.label }}</small>
          </div>
          <label v-if="(source.artifacts?.length || 0) > 1" class="artifact-picker">
            <span>Materiał</span>
            <select v-model="selectedArtifactId" aria-label="Wybierz materiał dokumentu">
              <option v-for="artifact in source.artifacts" :key="artifact.id" :value="artifact.id">{{ artifact.derived ? "Pochodny: " : "Oryginał: " }}{{ artifact.label }}</option>
            </select>
          </label>
        </div>
        <div class="original-viewer" :class="`is-${selectedArtifact.viewer}`">
          <component
            :is="artifactViewerTag(selectedArtifact)"
            :src="selectedArtifact.url"
            :alt="selectedArtifact.viewer === 'image' ? `${selectedArtifact.derived ? 'Materiał pochodny' : 'Oryginał'}: ${source.title}` : undefined"
            :title="selectedArtifact.viewer === 'image' ? undefined : `${selectedArtifact.derived ? 'Materiał pochodny' : 'Oryginał'}: ${source.title}`"
            loading="lazy"
            referrerpolicy="no-referrer"
            :sandbox="selectedArtifact.viewer === 'text' ? '' : undefined"
          />
        </div>
        <a class="original-open-link" :href="selectedArtifact.url" target="_blank" rel="noreferrer">Otwórz materiał w nowej karcie ↗</a>
      </section>

      <section class="transcript-pane">
        <div v-if="selectedReading" class="reading-primary-heading">
          <div>
            <p class="eyebrow">{{ selectedIsPrimary && readingModel.preferredLanguageMatched ? `Główny odczyt po ${readingModel.preferredLanguageLabel}` : selectedIsPrimary ? "Najlepszy dostępny odczyt" : "Wybrany odczyt dodatkowy" }}</p>
            <h3>{{ readingTypeLabel(selectedReading.type) }}</h3>
            <p class="reading-primary-meta">{{ readingStatusLabel(selectedReading) }} · {{ readingLanguages(selectedReading).join(" + ") || "język nieopisany" }} · v{{ selectedReading.version }}</p>
          </div>
          <button v-if="!selectedIsPrimary" class="return-primary-button" type="button" @click="selectReading(readingModel.primary)">Wróć do głównego</button>
        </div>

        <p v-if="selectedIsPrimary && selectedReading && !readingModel.preferredLanguageMatched" class="reading-fallback-note">Brak odczytu w głównym języku interfejsu. Pokazujemy najlepszą dostępną wersję bez zmiany jej znaczenia ani statusu kontroli.</p>

        <template v-if="displayedSegments.length">
          <div v-for="segment in displayedSegments" :key="`${selectedReading?.id}-${segment.sequence}`" class="segment">
            <div class="segment-label">{{ [segment.page, segment.language].filter(Boolean).join(" · ") || `segment ${segment.sequence}` }}</div>
            <TranscriptContent :text="segment.text" />
          </div>
        </template>
        <div v-else class="no-content"><h3>Brak transkrypcji w tym odczycie</h3><p>Źródło nie ma jeszcze segmentów tekstowych do prezentacji.</p></div>

        <details v-if="supplementalLanguageSegments.length" class="reading-language-supplement">
          <summary>Tekst źródłowy w tym odczycie · {{ supplementalLanguageSegments.length }}</summary>
          <div v-for="segment in supplementalLanguageSegments" :key="`${selectedReading?.id}-source-${segment.sequence}`" class="segment">
            <div class="segment-label">{{ [segment.page, segment.language].filter(Boolean).join(" · ") || `segment ${segment.sequence}` }}</div>
            <TranscriptContent :text="segment.text" />
          </div>
        </details>

        <details v-if="readingModel.supplemental.length" class="reading-version-picker">
          <summary>Pozostałe odczyty i wersje · {{ readingModel.supplemental.length }}</summary>
          <p>Wybierz tylko wtedy, gdy chcesz porównać język źródłowy, metodę odczytu albo wcześniejszą rewizję.</p>
          <div class="reading-version-list">
            <button
              v-for="item in readingModel.supplemental"
              :key="item.reading.id"
              type="button"
              class="reading-version-option"
              :class="{ 'is-active': item.reading.id === selectedReading?.id }"
              @click="selectReading(item.reading)"
            >
              <strong>{{ versionLabel(item.reading) }}</strong>
              <span>{{ readingStatusLabel(item.reading) }}<template v-if="item.olderVersion"> · wcześniejsza wersja</template></span>
            </button>
          </div>
        </details>
      </section>
    </div>
  </div>
</template>
