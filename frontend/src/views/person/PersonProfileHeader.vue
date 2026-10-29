<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import type { PersonProfileViewModel } from "../../models/person-profile-projector";
import UiIcon from "../../components/UiIcon.vue";

const props = defineProps<{
  model: PersonProfileViewModel;
  canOpenTree: boolean;
  canOpenGraph: boolean;
  canOpenMap: boolean;
}>();
const emit = defineEmits<{ navigate: [view: "tree" | "graph" | "map"] }>();
const heading = ref<HTMLHeadingElement | null>(null);
const copied = ref(false);

interface BirthYearField {
  value: string;
  notice: string;
}

function birthYearField(model: PersonProfileViewModel): BirthYearField {
  const isExternalProfile = model.identityScope === "presentation_only" && model.sourceReportedMemberships.length > 0;
  const withExternalNotice = (notice: string) => isExternalProfile
    ? `Niezweryfikowana informacja z drzewa zewnętrznego. ${notice}`
    : notice;

  if (model.identityScope === "hypothesis_group") {
    return {
      value: "Nieustalony",
      notice: "Grupa do rozstrzygnięcia nie przedstawia roku urodzenia jednej osoby.",
    };
  }

  const birth = model.lifeSpan.birth;
  const year = birth?.year;
  if (!birth || typeof year !== "number" || !Number.isFinite(year) || !Number.isInteger(year) || year <= 0) {
    return { value: "Nieustalony", notice: withExternalNotice("Rok urodzenia nie jest ustalony.") };
  }

  const notice = (value: string, detail = ""): BirthYearField => ({
    value,
    notice: withExternalNotice(detail),
  });
  switch (birth.qualifier) {
    case "exact":
      return notice(String(year));
    case "about":
      return notice(`ok. ${year}?`, birth.basis === "age_in_source"
        ? "Szacunek z wieku podanego w akcie; nie jest datą potwierdzoną aktem urodzenia."
        : "Przybliżony rok według zapisanej podstawy; dokładna data pozostaje nieustalona.");
    case "before":
      return notice(`przed ${year}`, "To granica roku urodzenia, nie dokładna data.");
    case "after":
      return notice(`po ${year}`, "To granica roku urodzenia, nie dokładna data.");
    default:
      return { value: "Nieustalony", notice: withExternalNotice("Nieznany kwalifikator roku urodzenia; wartość nie jest przedstawiana jako dokładna.") };
  }
}

const displayedBirthYear = computed(() => birthYearField(props.model));

async function copyLink(): Promise<void> {
  await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}${window.location.search}`);
  copied.value = true;
  window.setTimeout(() => { copied.value = false; }, 1500);
}

onMounted(() => heading.value?.focus({ preventScroll: true }));
</script>

<template>
  <header class="person-profile-hero">
    <div class="person-profile-identity">
      <p class="eyebrow">{{ model.familyKnown ? model.familyKnown.label : model.sourceReportedMemberships.length ? "KANDYDAT Z DRZEWA ZEWNĘTRZNEGO" : model.identityScope === "hypothesis_group" ? "WĘZEŁ DO ROZSTRZYGNIĘCIA" : model.treeEvidenceStatus === "unresolved" ? "OSOBA DO ROZSTRZYGNIĘCIA" : "PROFIL OSOBY" }}</p>
      <h1 ref="heading" tabindex="-1">{{ model.displayName }}</h1>
      <p class="person-life-span" :class="{ 'is-estimated': model.lifeSpan.estimated || model.lifeSpan.status !== 'complete' }">
        <strong>{{ model.lifeSpanLabel }}</strong>
        <span>{{ model.lifeSpanNotice }}</span>
      </p>
      <dl class="person-birth-year">
        <dt>Rok urodzenia</dt>
        <dd><strong>{{ displayedBirthYear.value }}</strong><span v-if="displayedBirthYear.notice">{{ displayedBirthYear.notice }}</span></dd>
      </dl>
      <p class="person-source-boundary"><strong>{{ model.sourceBoundaryLabel }}</strong><span>To zakres dokumentów, nie lata życia.</span></p>
      <p v-if="model.placeSequence.length" class="person-place-sequence">{{ model.placeSequence.join(" → ") }}</p>
    </div>
    <div class="person-profile-actions" aria-label="Perspektywy tej osoby">
      <RouterLink v-if="canOpenTree" to="/tree" @click="emit('navigate', 'tree')"><UiIcon name="tree" />Pokaż w drzewie</RouterLink>
      <RouterLink v-if="canOpenGraph" to="/graph" @click="emit('navigate', 'graph')"><UiIcon name="network" />Otwórz FAN</RouterLink>
      <RouterLink v-if="canOpenMap" to="/map" @click="emit('navigate', 'map')"><UiIcon name="map" />Pokaż na mapie</RouterLink>
      <button type="button" @click="copyLink"><UiIcon name="copy" />{{ copied ? "Skopiowano" : "Kopiuj link" }}</button>
    </div>
    <ul class="person-profile-statuses" aria-label="Oddzielne statusy profilu">
      <li><span>Tożsamość</span><strong>{{ model.identityLabel }}</strong></li>
      <li><span>Dane w drzewie</span><strong>{{ model.treeEvidenceLabel }}</strong></li>
      <li v-if="model.familyKnown"><span>Rozpoznanie rodzinne</span><strong>{{ model.familyKnown.label }}</strong><span>{{ model.familyKnown.confidenceLabel }} · {{ model.familyKnown.basisLabel }}</span></li>
      <li v-if="model.sourceReportedMemberships.length"><span>Sieci zewnętrzne</span><strong>{{ model.sourceReportedMemberships.length }} · imported_unverified</strong></li>
      <li><span>Jawne ustalenia</span><strong>{{ model.metrics.facts }}</strong></li>
      <li><span>{{ model.sourceReportedMemberships.length ? "Wystąpienia źródłowe" : "Wystąpienia w aktach" }}</span><strong>{{ model.metrics.occurrences }}</strong></li>
      <li><span>Konflikty i pytania</span><strong>{{ model.metrics.conflicts + model.metrics.questions }}</strong></li>
      <li><span>Do przeglądu</span><strong>{{ model.metrics.reviewItems }}</strong></li>
    </ul>
  </header>
</template>

<style scoped>
.person-birth-year { display: grid; gap: 2px; margin: 0 0 7px; }
.person-birth-year dt { color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.person-birth-year dd { display: grid; gap: 2px; margin: 0; }
.person-birth-year strong { color: var(--ink); font: 700 16px/1.2 Georgia, serif; }
.person-birth-year span { max-width: 760px; color: var(--muted); font-size: 13px; line-height: 1.45; }
</style>
