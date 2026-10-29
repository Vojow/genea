<script setup lang="ts">
import { computed } from "vue";

import Badge from "./Badge.vue";
import type { ResearchBacklogItem } from "../types/presentation";
import { parseResearchQuestionAssessment, researchAssessmentDispositionLabels } from "../types/presentation";
import { polishCount, statusLabel } from "../utils/formatting";

const props = withDefaults(defineProps<{ item: ResearchBacklogItem; compact?: boolean }>(), { compact: false });
const assessment = computed(() => parseResearchQuestionAssessment(props.item.assessment));
const hasAssessment = computed(() => Object.hasOwn(props.item, "assessment"));
const primaryTarget = computed(() => props.item.targets?.find((target) => target.primary) || props.item.targets?.[0]);
const score = computed(() => props.item.progress?.score || 0);
const firstGate = computed(() => props.item.progress?.gates?.[0]);
const duplicateCount = computed(() => props.item.potentialDuplicateQuestionIds?.length || 0);
const latestResearch = computed(() => props.item.recentResearch?.[0]);
const latestCoverage = computed(() => latestResearch.value?.coverage);
const scopePath = computed(() => (props.item.primaryScopePath || []).map((part) => part.title));
const supportingScopes = computed(() => (props.item.scopes || []).filter((scope) => !scope.primary));
const places = computed(() => {
  const targets = [
    ...(props.item.targets || []),
    ...(props.item.scopes || []).flatMap((scope) => scope.targets || []),
  ];
  return [...new Map(targets
    .filter((target) => target.type === "place" || target.type === "place_study")
    .map((target) => [target.id || target.label, target.label])).values()];
});

function outcomeLabel(outcome?: string): string {
  return ({
    positive: "wynik dodatni",
    negative: "wynik ujemny",
    inconclusive: "wynik niekonkluzywny",
    "not-searched": "nie przeszukano",
  } as Record<string, string>)[outcome || ""] || outcome || "wynik badania";
}
</script>

<template>
  <article class="question-card" :class="[`origin-${item.origin}`, { 'is-compact': compact }]">
    <header>
      <Badge :kind="item.origin === 'deterministic' ? 'is-warning' : 'is-good'">{{ item.origin === "deterministic" ? "wykryte regułą" : "kuratorowane" }}</Badge>
      <Badge>P{{ item.priority }}</Badge>
      <Badge v-if="item.answerStatus">Pytanie: {{ statusLabel(item.answerStatus) }}</Badge>
    </header>
    <p v-if="scopePath.length" class="question-scope-path">{{ scopePath.join(" › ") }}</p>
    <h3>{{ item.question }}</h3>
    <p v-if="primaryTarget" class="question-target">Cel: {{ primaryTarget.label }}</p>
    <div v-if="assessment" class="question-assessment" :data-disposition="assessment.disposition" :data-work-id="assessment.scopedWork.id">
      <p><small>Utrwalona ocena kuratora</small><strong>{{ researchAssessmentDispositionLabels[assessment.disposition] }}</strong></p>
      <p><b>Brakujący dowód:</b> {{ assessment.missingEvidence }}</p>
      <p><b>Następny krok:</b> {{ assessment.nextAction }}</p>
      <p><b>Warunek powrotu:</b> {{ assessment.returnCondition }}</p>
      <p><b>Praca w zakresie:</b> {{ statusLabel(assessment.scopedWork.itemType) }} · {{ statusLabel(assessment.scopedWork.status) }}</p>
    </div>
    <p v-else-if="hasAssessment" class="question-assessment-unavailable">Utrwalona ocena jest niedostępna w tym eksporcie.</p>
    <div v-else class="question-progress" :title="item.progress?.meaning || 'Postęp pracy badawczej'">
      <div class="question-progress-label"><span>Postęp pracy</span><strong>{{ score }}%</strong></div>
      <div class="question-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="score"><span :style="{ width: `${score}%` }"></span></div>
    </div>
    <p v-if="!hasAssessment && firstGate" class="question-gate">{{ firstGate }}</p>
    <div v-if="!hasAssessment && item.nextAction" class="question-next"><small>Następny krok</small><span>{{ item.nextAction }}</span></div>
    <div v-if="latestResearch" class="question-result">
      <small>Ostatni zapis · {{ outcomeLabel(latestResearch.outcome) }}</small>
      <strong v-if="latestResearch.summary">{{ latestResearch.summary }}</strong>
      <span><b>Zakres:</b> {{ latestResearch.scope }}</span>
      <span v-if="latestCoverage" class="question-coverage" :class="{ 'is-incomplete': !latestCoverage.complete || !latestCoverage.systematic }">
        <b>Pokrycie:</b>
        {{ latestCoverage.complete && latestCoverage.systematic ? "kompletne i systematyczne" : "niepełne lub niesystematyczne" }}
        <template v-if="latestCoverage.assessment"> — {{ latestCoverage.assessment }}</template>
      </span>
    </div>
    <p v-if="duplicateCount" class="question-duplicate">{{ polishCount(duplicateCount, "możliwy duplikat", "możliwe duplikaty", "możliwych duplikatów") }} — sprawdź przed triage.</p>
    <details v-if="!compact" class="question-details">
      <summary>Kryterium i stan procesu</summary>
      <p>{{ item.completionCriterion }}</p>
      <ul v-if="!hasAssessment">
        <li v-for="dimension in item.progress?.dimensions || []" :key="dimension.label" :class="{ 'is-done': dimension.done }">{{ dimension.done ? "✓" : "○" }} {{ dimension.label }}</li>
      </ul>
    </details>
    <footer>
      <Badge v-if="item.primaryScopeTitle">{{ item.primaryScopeTitle }}</Badge>
      <Badge v-for="scope in supportingScopes" :key="scope.id">także: {{ scope.title }}</Badge>
      <Badge v-for="place in places" :key="place">miejsce: {{ place }}</Badge>
      <Badge>legacy: {{ item.caseId }}</Badge>
      <Badge v-if="item.metrics?.logs">{{ item.metrics.logs }} logów</Badge>
      <Badge v-if="item.metrics?.coverageAssessments">{{ item.metrics.coverageAssessments }} ocen pokrycia</Badge>
      <Badge v-if="item.metrics?.openActions">{{ item.metrics.openActions }} działań</Badge>
    </footer>
  </article>
</template>

<style scoped>
.question-assessment { overflow-wrap: anywhere; }
.question-assessment p { margin: 8px 0; }
.question-assessment small, .question-assessment strong { display: block; }
</style>
