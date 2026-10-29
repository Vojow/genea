<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";

import { ROUTE_NAMES } from "../router";
import { auditEntityReferences, EntityIndex } from "../routing/entities";
import { useAppStore } from "../stores/app";
import EntityBreadcrumbs from "../components/EntityBreadcrumbs.vue";
import EntityLink from "../components/EntityLink.vue";
import RecentEntities from "../components/RecentEntities.vue";
import WorkspaceHeader from "../components/WorkspaceHeader.vue";
import { statusLabel } from "../utils/formatting";
import { loadResearchTelemetry, metricValueLabel, TelemetryApiError, TELEMETRY_METRICS, type ResearchTelemetry, type TelemetryRow } from "../api/research-telemetry";


const store = useAppStore();
const route = useRoute();
const index = computed(() => new EntityIndex(store.payload!));
const cases = computed(() => index.value.all("case"));
const questions = computed(() => index.value.all("question"));
const volumes = computed(() => index.value.all("volume"));
const activeQuestions = computed(() => questions.value.filter((item) => item.status !== "answered" && item.status !== "closed"));
const candidateQuestions = computed(() => questions.value.filter((item) => item.raw?.origin === "deterministic"));
const conflicts = computed(() => store.payload?.conflicts || []);
const claims = computed(() => store.payload?.claims || []);
const interruptedRuns = computed(() => index.value.all("run").filter((item) => !["complete", "completed", "accepted"].includes(item.status || "")));
const referenceIssues = computed(() => auditEntityReferences(index.value));
const workflowLanes = computed(() => (store.payload?.researchBacklog?.workflowStages || []).map((stage) => ({
  stage,
  label: statusLabel(stage),
  items: questions.value.filter((entry) => entry.status === stage),
})));
const executionSummary = computed(() => store.payload?.researchBacklog?.execution?.summary);
const nextQuestions = computed(() => activeQuestions.value.slice(0, 5));
// Private telemetry is ephemeral component state, separate from the presentation store.
const telemetry = shallowRef<ResearchTelemetry | null>(null);
const telemetryLoading = ref(false);
const telemetryError = ref("");
const telemetryGrouping = ref<"batches" | "scopes">("batches");
const telemetryRows = computed(() => telemetry.value?.[telemetryGrouping.value] || []);
const qaMetrics = [["correct_acts", "Poprawnie odczytane akty"], ["accepted_fields", "Zaakceptowane pola"], ["resolved_pairs", "Rozstrzygnięte pary"]] as const;
const scopeKindLabel = { act: "Akt", person: "Osoba / wystąpienie", generation: "Pokolenie", group: "Grupa", batch: "Partia", volume: "Księga", case: "Sprawa" };
let telemetryRequest: AbortController | null = null;

async function refreshTelemetry(): Promise<void> {
  telemetryRequest?.abort();
  const request = new AbortController();
  telemetryRequest = request;
  telemetryLoading.value = true;
  telemetryError.value = "";
  telemetry.value = null;
  try {
    const result = await loadResearchTelemetry(request.signal);
    if (!request.signal.aborted) telemetry.value = result;
  } catch (error) {
    if (!request.signal.aborted) telemetryError.value = error instanceof TelemetryApiError ? error.message : "Nie udało się pobrać telemetrii badań.";
  } finally {
    if (!request.signal.aborted) telemetryLoading.value = false;
  }
}

function completeness(row: TelemetryRow): string {
  const missing = TELEMETRY_METRICS.filter(([name]) => !row.metrics[name] || row.metrics[name]?.status === "unknown").map(([, label]) => label);
  const partial = TELEMETRY_METRICS.filter(([name]) => row.metrics[name]?.status === "partial").map(([, label]) => label);
  return [partial.length ? `Częściowe (partial): ${partial.join(", ")}.` : "", missing.length ? `Nieznane (unknown): ${missing.join(", ")}.` : "", !partial.length && !missing.length ? "Pełny pomiar prezentowanych metryk; estymacje zachowują własne oznaczenie." : ""].filter(Boolean).join(" ");
}

watch(() => route.name, (name) => {
  if (name === ROUTE_NAMES.researchDashboard) void refreshTelemetry();
  else {
    telemetryRequest?.abort();
    telemetry.value = null;
    telemetryLoading.value = false;
    telemetryError.value = "";
  }
}, { immediate: true });
onBeforeUnmount(() => telemetryRequest?.abort());

const evidenceLinks = (value: unknown) => {
  const item = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return ([
    ["source", item.sourceId], ["event", item.eventId], ["person", item.personId],
    ["place", item.placeId], ["case", item.caseId], ["question", item.questionId],
  ] as const).map(([kind, id]) => ({ kind, id: typeof id === "string" ? id : "" })).filter((link) => link.id);
};
</script>

<template>
  <div class="research-routed-layout">
    <section class="research-routed-content">
      <EntityBreadcrumbs workspace="Badania" />
      <template v-if="route.name === ROUTE_NAMES.researchDashboard">
        <WorkspaceHeader eyebrow="Pulpit badań" title="Co wymaga teraz uwagi?" description="Najbliższe decyzje kuratora, aktywne sprawy i jakość materiału — bez mieszania postępu pracy z pewnością genealogiczną." />

        <div class="research-kpi-grid" aria-label="Najważniejsze liczniki badań">
          <RouterLink to="/research/cases" class="research-kpi"><span>Aktywne sprawy</span><strong>{{ cases.length }}</strong><small>zakresy z pytaniami i następnymi krokami</small></RouterLink>
          <RouterLink to="/research/questions" class="research-kpi is-attention"><span>Do triage</span><strong>{{ candidateQuestions.length }}</strong><small>propozycje generatora czekające na decyzję</small></RouterLink>
          <RouterLink to="/research/evidence" class="research-kpi is-conflict"><span>Konflikty</span><strong>{{ conflicts.length }}</strong><small>rozbieżności wymagające korelacji</small></RouterLink>
          <RouterLink to="/research/questions" class="research-kpi"><span>Otwarte pytania</span><strong>{{ activeQuestions.length }}</strong><small>postęp pracy, nie prawdopodobieństwo tezy</small></RouterLink>
          <RouterLink to="/research/questions" class="research-kpi is-ready"><span>Gotowe kwerendy</span><strong>{{ executionSummary?.readyQueries || 0 }}</strong><small>specyfikacje read-only, nadal wymagają uruchomienia i oceny</small></RouterLink>
        </div>

        <div class="research-dashboard-columns">
          <section class="research-work-card">
            <header><div><p class="eyebrow">Zakresy</p><h2>Aktywne sprawy</h2></div><RouterLink to="/research/cases">Zobacz wszystkie</RouterLink></header>
            <div class="entity-stack"><EntityLink v-for="item in cases.slice(0, 5)" :key="item.id" kind="case" :id="item.id" :label="item.label" :status="item.status" /></div>
            <p v-if="!cases.length" class="empty-note">Brak aktywnych spraw w dopuszczonym eksporcie.</p>
          </section>

          <section class="research-work-card">
            <header><div><p class="eyebrow">Kolejka</p><h2>Najbliższe pytania</h2></div><RouterLink to="/research/questions">Otwórz backlog</RouterLink></header>
            <div class="entity-stack"><EntityLink v-for="item in nextQuestions" :key="item.id" kind="question" :id="item.id" :label="item.label" :status="item.status" /></div>
          </section>

          <aside class="research-health-card">
            <div><p class="eyebrow">Kontrola systemu</p><h2>Jakość i telemetria</h2></div>
            <dl>
              <div><dt>Woluminy</dt><dd>{{ volumes.length }}</dd></div>
              <div><dt>Przerwane przebiegi</dt><dd>{{ interruptedRuns.length }}</dd></div>
              <div><dt>Niedostępne referencje</dt><dd>{{ referenceIssues.length }}</dd></div>
              <div><dt>Koszt z ledgerów</dt><dd class="telemetry-health-value">{{ telemetryLoading ? 'Wczytywanie…' : metricValueLabel(telemetry?.summary.metrics.cost_usd) }}</dd></div>
            </dl>
            <p class="method-note">Brak telemetrii pozostaje brakiem danych. UI nie rekonstruuje kosztu ani dokładności z opisów tekstowych.</p>
          </aside>
        </div>
        <section class="research-work-card telemetry-panel" aria-labelledby="telemetry-title" :aria-busy="telemetryLoading">
          <header>
            <div><p class="eyebrow">Prywatne dane operacyjne</p><h2 id="telemetry-title">Nakład i wyniki</h2></div>
            <button type="button" :disabled="telemetryLoading" @click="refreshTelemetry">Odśwież telemetrię</button>
          </header>
          <p v-if="telemetryLoading" role="status">Wczytywanie prywatnej telemetrii…</p>
          <p v-else-if="telemetryError" role="alert">{{ telemetryError }} Telemetria jest niedostępna; brak pomiaru nie oznacza zera.</p>
          <template v-else-if="telemetry">
            <p v-if="telemetry.status === 'empty'" class="empty-note" role="status">Brak dopuszczonych danych telemetrii w tym zakresie.</p>
            <template v-else>
              <p class="telemetry-summary">Unikalne zdarzenia: {{ telemetry.summary.eventCount }}. Koszt unii zdarzeń: {{ metricValueLabel(telemetry.summary.metrics.cost_usd) }}. Sumy kart zakresów nie są sumą wydatków.</p>
              <fieldset class="telemetry-grouping"><legend>Podmiot raportu</legend><label><input v-model="telemetryGrouping" type="radio" value="batches" name="telemetry-grouping" /> Partie</label><label><input v-model="telemetryGrouping" type="radio" value="scopes" name="telemetry-grouping" /> Powiązane zakresy</label></fieldset>
              <p v-if="!telemetryRows.length" class="empty-note">Brak dopuszczonych podmiotów w tym zestawieniu.</p>
              <article v-for="row in telemetryRows" :key="row.id" class="telemetry-row" :data-telemetry-id="row.id">
                <header><div><p class="eyebrow">Podmiot · {{ row.scope ? scopeKindLabel[row.scope.kind] : 'Partia' }}</p><h3>{{ row.label }}</h3></div><span>{{ row.eventCount }} unikalnych zdarzeń</span></header>
                <p v-if="row.scope?.kind === 'generation'">Anchor: <code>{{ row.scope.anchorId }}</code> · głębokość: {{ row.scope.depth }}</p>
                <dl class="telemetry-columns">
                  <div><dt>Wynik</dt><dd><p>Stan work items: brak źródła (unknown).</p><ul><li v-for="[name, label] in qaMetrics" :key="name">{{ label }}: {{ metricValueLabel(row.metrics[name]) }}</li></ul><p>Liczniki QA z ledgeru nie ustalają tożsamości ani relacji.</p></dd></div>
                  <div><dt>Nakład</dt><dd><p v-if="row.scope">Wspólny nakład powiązanych operacji; bez podziału kosztu na osoby.</p><ul><li v-for="[name, label] in TELEMETRY_METRICS" :key="name" :data-metric="name" :data-measurement-status="row.metrics[name]?.status || 'unknown'"><span>{{ label }}: {{ metricValueLabel(row.metrics[name]) }}</span><small v-if="row.metrics[name]?.unknownObservations">Brak pomiaru w {{ row.metrics[name]?.unknownObservations }} obserwacjach.</small></li></ul></dd></div>
                  <div><dt>Kompletność</dt><dd>{{ completeness(row) }}</dd></div>
                  <div><dt>Blocker</dt><dd>Brak źródła blockerów work items (unknown).</dd></div>
                </dl>
                <details><summary>Identyfikatory audytowe</summary><dl class="telemetry-audit"><div><dt>ID podmiotu</dt><dd><code>{{ row.scope?.id || row.id }}</code></dd></div><div><dt>Partie</dt><dd><code v-for="id in row.batchIds" :key="id">{{ id }}</code></dd></div><div><dt>Zdarzenia</dt><dd><code v-for="id in row.eventIds" :key="id">{{ id }}</code></dd></div></dl></details>
              </article>
            </template>
            <dl class="telemetry-sources"><div><dt>Zajętość dysku</dt><dd>brak źródła (unknown)</dd></div><div><dt>Pokrycie archiwum</dt><dd>brak źródła (unknown)</dd></div><div><dt>Czas kalendarzowy</dt><dd>brak źródła (unknown)</dd></div></dl>
          </template>
          <p class="method-note">Telemetryka opisuje nakład powiązanych operacji. Trafienia nie oznaczają użycia cache ani pełnego pokrycia źródła; reasoning jest częścią wyjścia. Brak metryki pozostaje nieznany.</p>
        </section>
        <RecentEntities />
      </template>

      <template v-else-if="route.name === ROUTE_NAMES.researchCases">
        <WorkspaceHeader eyebrow="Sprawy badawcze" title="Zakresy, pytania i następne kroki" description="Każda sprawa łączy pytania, zakres źródeł i jawne kryterium dalszej pracy." />
        <div class="entity-list-grid"><EntityLink v-for="item in cases" :key="item.id" kind="case" :id="item.id" :label="item.label" :status="item.status" /></div>
      </template>

      <template v-else-if="route.name === ROUTE_NAMES.researchQuestions">
        <WorkspaceHeader eyebrow="Pytania i backlog" title="Kuratorowane pytania i wykryte luki" description="Kandydatura deterministyczna nie staje się pytaniem bez triage. Kolumny korzystają z głównego przewijania strony, aby nie tworzyć zagnieżdżonych pułapek przewijania." />
        <div class="question-lanes">
          <section v-for="lane in workflowLanes" :key="lane.stage" :data-stage="lane.stage">
            <header class="question-lane-header"><div><span class="workflow-dot"></span><h2>{{ lane.label }}</h2></div><strong>{{ lane.items.length }}</strong></header>
            <div class="question-lane-scroll"><EntityLink v-for="item in lane.items" :key="item.id" kind="question" :id="item.id" :label="item.label" :status="String(item.raw?.origin || '')" /><p v-if="!lane.items.length" class="empty-note">Brak pytań na tym etapie.</p></div>
          </section>
        </div>
      </template>

      <template v-else-if="route.name === ROUTE_NAMES.researchVolumes">
        <WorkspaceHeader eyebrow="Woluminy i przebiegi" title="Indeksy, review i jakość AI" description="Telemetria występuje wyłącznie jako zmierzona, oszacowana albo nieznana." />
        <div class="entity-list-grid"><EntityLink v-for="item in volumes" :key="item.id" kind="volume" :id="item.id" :label="item.label" :status="item.status" /></div>
        <p v-if="!volumes.length" class="empty-state-card">Brak woluminów w bieżącym eksporcie prezentacyjnym. Indeksy pozostają w warstwie badawczej do czasu jawnego dopuszczenia.</p>
      </template>

      <template v-else-if="route.name === ROUTE_NAMES.researchEvidence">
        <WorkspaceHeader eyebrow="Jakość dowodów" title="Twierdzenia i konflikty" description="Źródło nie jest automatycznie dowodem tezy. Status prezentacyjny nie uruchamia writebacku." />
        <div class="evidence-columns">
          <section><header class="evidence-column-header"><div><p class="eyebrow">Ustalenia</p><h2>Twierdzenia</h2></div><strong>{{ claims.length }}</strong></header><div class="evidence-scroll"><article v-for="claim in claims" :key="claim.id || claim.statement"><div class="evidence-item-heading"><strong>{{ claim.statement }}</strong><span class="entity-status">{{ statusLabel(claim.status) }}</span></div><p>{{ claim.rationale }}</p><EntityLink v-for="link in evidenceLinks(claim)" :key="`${link.kind}:${link.id}`" :kind="link.kind" :id="link.id" :label="index.resolve(link.kind, link.id).label" :availability="index.resolve(link.kind, link.id).availability" /></article></div></section>
          <section><header class="evidence-column-header"><div><p class="eyebrow">Rozbieżności</p><h2>Konflikty</h2></div><strong>{{ conflicts.length }}</strong></header><div class="evidence-scroll"><article v-for="conflict in conflicts" :key="conflict.id || conflict.title"><div class="evidence-item-heading"><strong>{{ conflict.title }}</strong><span class="entity-status">{{ statusLabel(conflict.status || 'open') }}</span></div><p>{{ conflict.description }}</p><EntityLink v-for="link in evidenceLinks(conflict)" :key="`${link.kind}:${link.id}`" :kind="link.kind" :id="link.id" :label="index.resolve(link.kind, link.id).label" :availability="index.resolve(link.kind, link.id).availability" /></article></div></section>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.telemetry-panel { margin-top: 20px; min-width: 0; }
.telemetry-panel > header { flex-wrap: wrap; align-items: center; }
.telemetry-panel button { padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; color: var(--forest); background: var(--paper, #fffdf8); cursor: pointer; }
.telemetry-panel button:disabled { cursor: wait; opacity: .65; }
.telemetry-panel button:focus-visible, .telemetry-panel summary:focus-visible, .telemetry-grouping input:focus-visible { outline: 2px solid var(--forest); outline-offset: 3px; }
.telemetry-panel p { margin: 0; line-height: 1.6; }
.telemetry-grouping { display: flex; flex-wrap: wrap; gap: 12px; border: 1px solid var(--line); border-radius: 8px; }
.telemetry-grouping label { display: inline-flex; align-items: center; gap: 6px; }
.telemetry-row { min-width: 0; padding: 16px; border: 1px solid var(--line); border-radius: 10px; }
.telemetry-row > header { display: flex; flex-wrap: wrap; align-items: start; justify-content: space-between; gap: 8px; }
.telemetry-row h3 { margin: 0; font-size: 19px; line-height: 1.4; }
.telemetry-columns { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr); gap: 16px; margin: 16px 0; }
.telemetry-columns > div { min-width: 0; }
.telemetry-columns dt, .telemetry-sources dt, .telemetry-audit dt { font-weight: 700; }
.telemetry-columns dd, .telemetry-sources dd, .telemetry-audit dd { margin: 6px 0 0; line-height: 1.6; }
.telemetry-columns ul { margin: 8px 0; padding-left: 18px; }
.telemetry-columns li { margin-bottom: 6px; }
.telemetry-columns small { display: block; }
.telemetry-panel, .telemetry-health-value { word-break: normal; overflow-wrap: normal; hyphens: none; }
.telemetry-health-value { font: inherit; font-size: 13px; line-height: 1.5; }
.telemetry-row summary { cursor: pointer; }
.telemetry-audit { display: grid; gap: 8px; }
.telemetry-audit code { display: inline-block; max-width: 100%; margin-right: 8px; overflow-wrap: anywhere; }
.telemetry-row > p code { overflow-wrap: anywhere; }
.telemetry-sources { display: flex; flex-wrap: wrap; gap: 16px 28px; margin: 0; }
@media (max-width: 1100px) { .telemetry-columns { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 650px) { .telemetry-columns { grid-template-columns: minmax(0, 1fr); } .telemetry-row { padding: 12px; } }
</style>
