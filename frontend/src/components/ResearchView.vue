<script setup lang="ts">
import { computed, ref, watch } from "vue";

import Badge from "./Badge.vue";
import ResearchQuestionCard from "./ResearchQuestionCard.vue";
import type {
  CuratorAccess,
  PresentationClaim,
  PresentationConflict,
  ResearchBacklog,
  ResearchBacklogItem,
  VolumeIndexCandidate,
  VolumeIndexSummary,
} from "../types/presentation";
import { polishCount, statusLabel } from "../utils/formatting";

type ResearchTab = "backlog" | "gaps" | "indexes" | "claims" | "conflicts";
type BacklogLane = {
  scopeId: string;
  scopeTitle: string;
  scopeType: string;
  caseId: string;
  caseTitle: string;
  items: ResearchBacklogItem[];
  stages: Array<{ stageId: string; items: ResearchBacklogItem[] }>;
};

const props = defineProps<{
  backlog?: ResearchBacklog;
  claims: PresentationClaim[];
  conflicts: PresentationConflict[];
  volumeIndexes?: VolumeIndexSummary[];
  curatorAccess?: CuratorAccess;
}>();

const activeTab = ref<ResearchTab>("backlog");
const query = ref("");
const caseId = ref("");
const scopeId = ref("");
const placeId = ref("");
const stage = ref("");
const origin = ref("");

const safeBacklog = computed<ResearchBacklog>(() => props.backlog || { items: [], workflowStages: [], summary: { total: 0, candidates: 0 } });
const cases = computed(() => [...new Map(safeBacklog.value.items.map((item) => [item.caseId, item.caseTitle])).entries()]);
const scopes = computed(() => (safeBacklog.value.scopes || [])
  .filter((item) => item.type !== "program")
  .sort((left, right) => left.title.localeCompare(right.title, "pl")));
const places = computed(() => {
  const targets = safeBacklog.value.items.flatMap((item) => [
    ...(item.targets || []),
    ...(item.scopes || []).flatMap((scope) => scope.targets || []),
  ]).filter((target) => target.type === "place" || target.type === "place_study");
  return [...new Map(targets.map((target) => [target.id || target.label, target.label])).entries()]
    .sort((left, right) => left[1].localeCompare(right[1], "pl"));
});
const visibleItems = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase("pl-PL");
  return safeBacklog.value.items.filter((item) => {
    const recentResearchText = (item.recentResearch || []).flatMap((activity) => [
      activity.query,
      activity.repository,
      activity.scope,
      activity.summary,
      activity.coverage?.assessment,
    ]);
    const scopeText = (item.scopes || []).flatMap((scope) => [scope.title, ...(scope.primaryPath || []).map((part) => part.title), ...(scope.targets || []).map((target) => target.label)]);
    const itemPlaceIds = [
      ...(item.targets || []),
      ...(item.scopes || []).flatMap((scope) => scope.targets || []),
    ].filter((target) => target.type === "place" || target.type === "place_study").map((target) => target.id || target.label);
    const searchText = [item.question, item.caseId, item.caseTitle, item.completionCriterion, ...(item.targets || []).map((target) => target.label), ...scopeText, ...recentResearchText]
      .filter(Boolean).join(" ").toLocaleLowerCase("pl-PL");
    return (!normalized || searchText.includes(normalized))
      && (!caseId.value || item.caseId === caseId.value)
      && (!scopeId.value || (item.scopeIds || []).includes(scopeId.value))
      && (!placeId.value || itemPlaceIds.includes(placeId.value))
      && (!stage.value || item.workflowStage === stage.value)
      && (!origin.value || item.origin === origin.value);
  });
});
const visibleSummary = computed(() => ({
  visible: visibleItems.value.length,
  curated: visibleItems.value.filter((item) => item.origin === "curated").length,
  deterministic: visibleItems.value.filter((item) => item.origin === "deterministic").length,
}));
const lanes = computed<BacklogLane[]>(() => [...new Map(visibleItems.value.map((item) => {
  const laneScopeId = item.primaryScopeId || `legacy:${item.caseId}`;
  return [laneScopeId, {
    scopeId: laneScopeId,
    scopeTitle: item.primaryScopeTitle || item.caseTitle,
    scopeType: item.primaryScopeType || "legacy_case",
    caseId: item.caseId,
    caseTitle: item.caseTitle,
  }];
})).values()].map((group) => {
  const items = visibleItems.value.filter((item) => (item.primaryScopeId || `legacy:${item.caseId}`) === group.scopeId);
  return {
    ...group,
    items,
    stages: safeBacklog.value.workflowStages
      .filter((stageId) => items.some((item) => item.workflowStage === stageId))
      .map((stageId) => ({ stageId, items: items.filter((item) => item.workflowStage === stageId) })),
  };
}));
const candidates = computed(() => safeBacklog.value.items.filter((item) => item.origin === "deterministic"));
const openConflicts = computed(() => props.conflicts.filter((item) => item.status !== "resolved"));
const selectedIndexId = ref("");
const selectedRegionId = ref("");
const correctedText = ref("");
const reviewNote = ref("");
const reviewStatus = ref("");
const safeVolumeIndexes = computed(() => props.volumeIndexes || []);
const selectedIndex = computed(() => safeVolumeIndexes.value.find((item) => item.id === selectedIndexId.value) || safeVolumeIndexes.value[0] || null);
const selectedCandidate = computed<VolumeIndexCandidate | null>(() => selectedIndex.value?.candidates.find((item) => item.regionId === selectedRegionId.value) || selectedIndex.value?.candidates[0] || null);
const overlayStyle = computed(() => {
  const item = selectedCandidate.value;
  if (!item?.bbox || item.bbox.length !== 4 || !item.width || !item.height) return null;
  const [left, top, right, bottom] = item.bbox;
  return {
    left: `${(left / item.width) * 100}%`,
    top: `${(top / item.height) * 100}%`,
    width: `${((right - left) / item.width) * 100}%`,
    height: `${((bottom - top) / item.height) * 100}%`,
  };
});

watch(selectedCandidate, (candidate) => {
  if (!candidate) return;
  correctedText.value = candidate.correctedText || candidate.rawText;
  reviewNote.value = candidate.reviewNote || "";
}, { immediate: true });

function selectCandidate(candidate: VolumeIndexCandidate): void {
  selectedRegionId.value = candidate.regionId;
  correctedText.value = candidate.correctedText || candidate.rawText;
  reviewNote.value = candidate.reviewNote || "";
  reviewStatus.value = "";
}

async function saveVolumeReview(decision: "hit" | "false_positive" | "illegible" | "defer"): Promise<void> {
  const index = selectedIndex.value;
  const candidate = selectedCandidate.value;
  if (!index || !candidate || !props.curatorAccess?.authenticated) return;
  reviewStatus.value = "Zapisywanie…";
  const response = await fetch(`/api/private/volume-index/${encodeURIComponent(index.id)}/review`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": props.curatorAccess.csrf },
    body: JSON.stringify({
      regionId: candidate.regionId,
      decision,
      correctedText: decision === "hit" ? correctedText.value : undefined,
      note: reviewNote.value || undefined,
      reviewer: "local-ui-curator",
    }),
  });
  if (!response.ok) {
    reviewStatus.value = "Nie udało się zapisać decyzji.";
    return;
  }
  reviewStatus.value = "Decyzja zapisana. Odświeżam widok…";
  window.setTimeout(() => window.location.reload(), 350);
}

const stageLabels: Record<string, string> = {
  detected: "Wykryte",
  triage: "Triage",
  ready: "Gotowe",
  in_progress: "W toku",
  blocked: "Zablokowane",
  correlation_review: "Korelacja",
  answered: "Odpowiedziane",
  dismissed: "Zamknięte",
};

function stageLabel(value: string): string {
  return stageLabels[value] || value;
}

function scopeTypeLabel(value: string): string {
  return ({
    program: "Program",
    lineage: "Linia",
    branch: "Gałąź",
    family_unit: "Rodzina",
    place_study: "Studium miejsca",
    theme: "Temat",
    legacy_case: "Sprawa legacy",
    other: "Inny zakres",
  } as Record<string, string>)[value] || value;
}
</script>

<template>
  <Teleport to="#research-view-host">
    <div class="section-heading compact">
      <div>
        <p class="eyebrow">Badania i jawna niepewność</p>
        <h1>Backlog pytań badawczych</h1>
        <p class="lead">Istniejące pytania i deterministycznie wykryte luki w jednym procesie — bez mieszania postępu pracy z pewnością tezy.</p>
      </div>
    </div>

    <div class="research-tabs segmented" role="tablist" aria-label="Widoki badań">
      <button data-research-tab="backlog" :class="{ 'is-active': activeTab === 'backlog' }" role="tab" :aria-selected="activeTab === 'backlog'" @click="activeTab = 'backlog'">Backlog <span id="backlog-count">{{ safeBacklog.summary?.total || 0 }}</span></button>
      <button data-research-tab="gaps" :class="{ 'is-active': activeTab === 'gaps' }" role="tab" :aria-selected="activeTab === 'gaps'" @click="activeTab = 'gaps'">Wykryte luki <span id="gap-count">{{ safeBacklog.summary?.candidates || 0 }}</span></button>
      <button data-research-tab="indexes" :class="{ 'is-active': activeTab === 'indexes' }" role="tab" :aria-selected="activeTab === 'indexes'" @click="activeTab = 'indexes'">Indeksy woluminów <span>{{ safeVolumeIndexes.length }}</span></button>
      <button data-research-tab="claims" :class="{ 'is-active': activeTab === 'claims' }" role="tab" :aria-selected="activeTab === 'claims'" @click="activeTab = 'claims'">Twierdzenia <span id="claim-count">{{ claims.length }}</span></button>
      <button data-research-tab="conflicts" :class="{ 'is-active': activeTab === 'conflicts' }" role="tab" :aria-selected="activeTab === 'conflicts'" @click="activeTab = 'conflicts'">Konflikty <span id="conflict-count">{{ openConflicts.length }}</span></button>
    </div>

    <div v-show="activeTab === 'backlog'" class="research-tab-panel" data-research-panel="backlog">
      <div class="research-toolbar">
        <label class="search-field research-search"><span aria-hidden="true">⌕</span><input id="backlog-search" v-model="query" type="search" placeholder="Szukaj pytania, wyniku, zakresu lub celu…" /></label>
        <label><span class="sr-only">Filtruj według zakresu</span><select id="backlog-scope-filter" v-model="scopeId" aria-label="Filtruj według zakresu"><option value="">Wszystkie linie i zakresy</option><option v-for="item in scopes" :key="item.id" :value="item.id">{{ scopeTypeLabel(item.type) }} — {{ item.title }}</option></select></label>
        <label><span class="sr-only">Filtruj według miejsca</span><select id="backlog-place-filter" v-model="placeId" aria-label="Filtruj według miejsca"><option value="">Wszystkie miejsca</option><option v-for="([id, title]) in places" :key="id" :value="id">{{ title }}</option></select></label>
        <label><span class="sr-only">Filtruj według sprawy legacy</span><select id="backlog-case-filter" v-model="caseId" aria-label="Filtruj według sprawy legacy"><option value="">Wszystkie sprawy legacy</option><option v-for="([id, title]) in cases" :key="id" :value="id">{{ id }} — {{ title }}</option></select></label>
        <label><span class="sr-only">Filtruj według etapu</span><select id="backlog-stage-filter" v-model="stage" aria-label="Filtruj według etapu"><option value="">Wszystkie etapy</option><option v-for="stageId in safeBacklog.workflowStages" :key="stageId" :value="stageId">{{ stageLabel(stageId) }}</option></select></label>
        <label><span class="sr-only">Filtruj według pochodzenia</span><select id="backlog-origin-filter" v-model="origin" aria-label="Filtruj według pochodzenia"><option value="">Wszystkie źródła pytań</option><option value="curated">Kuratorowane</option><option value="deterministic">Wykryte automatycznie</option></select></label>
      </div>
      <div id="backlog-summary" class="backlog-summary">
        <span><strong>{{ visibleSummary.visible }}</strong> widocznych</span>
        <span><strong>{{ visibleSummary.curated }}</strong> kuratorowanych</span>
        <span><strong>{{ visibleSummary.deterministic }}</strong> wykrytych luk</span>
        <small>Procent oznacza wykonanie procesu, nie pewność historyczną.</small>
      </div>
      <div id="backlog-board" class="backlog-board" aria-live="polite">
        <section v-for="lane in lanes" :key="lane.scopeId" class="backlog-lane">
          <div class="backlog-lane-title"><div><strong>{{ scopeTypeLabel(lane.scopeType) }}</strong><span>{{ lane.scopeTitle }}</span><small v-if="lane.caseId">legacy: {{ lane.caseId }}</small></div><Badge>{{ polishCount(lane.items.length, "pytanie", "pytania", "pytań") }}</Badge></div>
          <div class="backlog-stage-row">
            <section v-for="laneStage in lane.stages" :key="laneStage.stageId" class="backlog-stage" :class="`stage-${laneStage.stageId}`">
              <div class="backlog-stage-title"><h3>{{ stageLabel(laneStage.stageId) }}</h3><span>{{ laneStage.items.length }}</span></div>
              <div class="backlog-stage-cards"><ResearchQuestionCard v-for="item in laneStage.items" :key="item.id" :item="item" /></div>
            </section>
          </div>
        </section>
        <div v-if="!lanes.length" class="no-content">Brak pytań spełniających wybrane filtry.</div>
      </div>
    </div>

    <div v-show="activeTab === 'gaps'" class="research-tab-panel" data-research-panel="gaps">
      <div class="research-notice"><strong>Kandydatury, nie odpowiedzi.</strong><span>Każda luka wymaga triage. Generator nie tworzy ani nie zamyka kanonicznych pytań w SQLite.</span></div>
      <div id="gap-list" class="research-grid research-gap-grid"><ResearchQuestionCard v-for="item in candidates" :key="item.id" :item="item" compact /><div v-if="!candidates.length" class="no-content">Generator nie wykrył obecnie nowych luk w dozwolonym eksporcie.</div></div>
    </div>

    <div v-show="activeTab === 'indexes'" class="research-tab-panel" data-research-panel="indexes">
      <div class="research-notice"><strong>Pomoc lokalizacyjna, nie dowód.</strong><span>OCR/HTR i brak trafienia nie tworzą transkrypcji ani pokrycia księgi. Decyzja „trafienie” zawsze odnosi się do obrazu.</span></div>
      <div v-if="safeVolumeIndexes.length" class="volume-index-layout">
        <aside class="volume-index-sidebar">
          <label><span>Wolumin</span><select v-model="selectedIndexId" aria-label="Wybierz indeks woluminu"><option value="">{{ safeVolumeIndexes[0]?.label }}</option><option v-for="item in safeVolumeIndexes.slice(1)" :key="item.id" :value="item.id">{{ item.label }}</option></select></label>
          <div v-if="selectedIndex" class="volume-index-stats"><span><strong>{{ selectedIndex.unitCount || 0 }}</strong> jednostek</span><span><strong>{{ selectedIndex.regionCount || 0 }}</strong> regionów</span><span><strong>{{ selectedIndex.reviewedCount || 0 }}</strong> przejrzanych</span></div>
          <button v-for="candidate in selectedIndex?.candidates || []" :key="candidate.regionId" class="volume-candidate-button" :class="{ 'is-active': selectedCandidate?.regionId === candidate.regionId }" @click="selectCandidate(candidate)"><strong>{{ candidate.ordinal }} · {{ candidate.engine }}</strong><span>{{ candidate.rawText }}</span><small>{{ candidate.reviewDecision || "do przeglądu" }}</small></button>
        </aside>
        <section v-if="selectedCandidate" class="volume-review-panel">
          <header><div><p class="eyebrow">{{ selectedCandidate.locator }}</p><h2>Region {{ selectedCandidate.ordinal }}</h2></div><Badge>{{ selectedCandidate.reviewDecision || "nieprzejrzane" }}</Badge></header>
          <div class="volume-review-image"><img :src="selectedCandidate.renderUrl" :alt="`Strona ${selectedCandidate.ordinal}`" /><span v-if="overlayStyle" class="volume-region-overlay" :style="overlayStyle"></span></div>
          <div class="volume-review-comparison"><article><small>Wynik {{ selectedCandidate.engine }}</small><p>{{ selectedCandidate.rawText }}</p></article><label><small>Odczyt po kontroli obrazu</small><textarea v-model="correctedText" rows="3"></textarea></label></div>
          <label><small>Notatka review</small><textarea v-model="reviewNote" rows="2"></textarea></label>
          <div class="volume-review-actions"><button :disabled="!curatorAccess?.authenticated" @click="saveVolumeReview('hit')">Trafienie</button><button :disabled="!curatorAccess?.authenticated" @click="saveVolumeReview('false_positive')">Fałszywy wynik</button><button :disabled="!curatorAccess?.authenticated" @click="saveVolumeReview('illegible')">Nieczytelne</button><button :disabled="!curatorAccess?.authenticated" @click="saveVolumeReview('defer')">Odłóż</button></div>
          <p class="volume-review-status">{{ curatorAccess?.authenticated ? reviewStatus : "Odblokuj prywatne UI, aby zapisywać decyzje kuratora." }}</p>
        </section>
      </div>
      <div v-else class="no-content">Brak zarejestrowanych indeksów. Dodaj lokalny OUTPUT do <code>config/volume-indexes.json</code>; UI nie skanuje dysku automatycznie.</div>
    </div>

    <div v-show="activeTab === 'claims'" class="research-tab-panel" data-research-panel="claims">
      <div id="claim-list" class="research-grid research-card-grid">
        <article v-for="claim in claims" :key="claim.id || claim.statement" class="research-card"><Badge :kind="claim.status === 'Confirmed' ? 'is-good' : 'is-warning'">{{ statusLabel(claim.status) }}</Badge><h3>{{ claim.statement }}</h3><p>{{ claim.rationale || "Brak osobnego uzasadnienia w tym eksporcie." }}</p><footer><Badge v-if="claim.caseId">{{ claim.caseId }}</Badge><Badge v-if="claim.predicate">{{ claim.predicate }}</Badge></footer></article>
        <div v-if="!claims.length" class="no-content">Brak twierdzeń w eksporcie.</div>
      </div>
    </div>

    <div v-show="activeTab === 'conflicts'" class="research-tab-panel" data-research-panel="conflicts">
      <div id="conflict-list" class="research-grid research-card-grid">
        <article v-for="conflict in openConflicts" :key="conflict.id || conflict.title" class="research-card"><Badge kind="is-conflict">{{ statusLabel(conflict.status) }}</Badge><h3>{{ conflict.title }}</h3><p>{{ conflict.description }}</p><footer><Badge v-if="conflict.claimId">twierdzenie {{ conflict.claimId.slice(-6) }}</Badge></footer></article>
        <div v-if="!openConflicts.length" class="no-content">Brak otwartych konfliktów w eksporcie.</div>
      </div>
    </div>
  </Teleport>
</template>
