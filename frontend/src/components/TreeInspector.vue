<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import Badge from "./Badge.vue";
import OpenFullRecordLink from "./OpenFullRecordLink.vue";
import EntityLink from "./EntityLink.vue";
import type { TreeInspectorModel } from "../types/presentation";
import { statusLabel } from "../utils/formatting";
import { reviewBadgeKind } from "../../core/review-status.mjs";
import { useWorkspaceInspectorState } from "../composables/useWorkspaceInspectorState";

const props = defineProps<{ inspection: TreeInspectorModel | null }>();
type TabId = "summary" | "family" | "acts" | "research";

const ACTIVE_TAB_KEY = "genea:tree-inspector-tab:v1";
const activeTab = ref<TabId>("summary");
const availableTabs = computed(() => props.inspection?.tabs || []);
const selectionKey = computed(() => props.inspection?.personId || "");
const { state: sheetState } = useWorkspaceInspectorState({
  id: "tree",
  hostId: "tree-inspector",
  selectionKey,
  revealEvent: "genea:tree-person-selected",
});

onMounted(() => {
  const storedTab = sessionStorage.getItem(ACTIVE_TAB_KEY);
  if (["summary", "family", "acts", "research"].includes(storedTab || "")) activeTab.value = storedTab as TabId;
});

watch(() => props.inspection?.personId, () => {
  if (!availableTabs.value.some((tab) => tab.id === activeTab.value)) activeTab.value = "summary";
});
watch(activeTab, (value) => sessionStorage.setItem(ACTIVE_TAB_KEY, value));

function selectTab(id: TabId): void {
  activeTab.value = id;
  if (sheetState.value !== "expanded") sheetState.value = "expanded";
}
</script>

<template>
  <Teleport to="#tree-inspector">
    <div v-if="!inspection" class="empty-state"><span class="empty-glyph">⌘</span><h2>Wybierz osobę</h2><p>Zobacz podsumowanie, rodzinę, akty i otwarte pytania.</p></div>
    <div v-else class="tree-inspector-shell" :class="`is-${sheetState}`">
      <header class="tree-inspector-header">
        <div><span>{{ inspection.generation }}</span><strong>{{ inspection.name }}</strong></div>
        <div class="tree-inspector-size-controls" aria-label="Rozmiar panelu">
          <button :aria-pressed="sheetState === 'collapsed'" aria-label="Zwiń inspektor" @click="sheetState = 'collapsed'">−</button>
          <button :aria-pressed="sheetState === 'peek'" aria-label="Pokaż skrót inspektora" @click="sheetState = 'peek'">▱</button>
          <button :aria-pressed="sheetState === 'expanded'" aria-label="Rozwiń inspektor" @click="sheetState = 'expanded'">□</button>
        </div>
      </header>
      <div v-if="sheetState !== 'collapsed'" class="tree-inspector-body">
        <nav class="tree-inspector-tabs" aria-label="Sekcje karty osoby">
          <button v-for="tab in inspection.tabs" :key="tab.id" :class="{ 'is-active': activeTab === tab.id }" :aria-current="activeTab === tab.id ? 'page' : undefined" @click="selectTab(tab.id)">{{ tab.label }} <small>{{ tab.count }}</small></button>
        </nav>

        <div class="inspector-content">
          <section v-if="activeTab === 'summary'" class="tree-inspector-pane">
            <div class="inspector-kind"><span>{{ inspection.identityLabel }}</span><Badge :kind="inspection.evidenceKind">{{ inspection.evidence }}</Badge></div>
            <p v-if="inspection.familyKnown" class="tree-family-known-notice" role="status"><Badge>{{ inspection.familyKnown.label }}</Badge> {{ inspection.familyKnown.notice }}</p>
            <p v-if="inspection.identityNotice" class="tree-identity-notice" role="status">{{ inspection.identityNotice }}</p>
            <details v-for="review in inspection.localIdentityReviews" :key="review.decisionTier" class="tree-local-identity-review" :open="review.humanReviewOpen">
              <summary>{{ review.humanReviewLabel }}</summary>
              <strong>{{ review.label }}</strong>
              <p>Dotyczy wyłącznie wskazanych occurrences; nie zmienia osoby ani relacji.</p>
              <ul :aria-label="`Backing occurrences: ${review.humanReviewLabel}`"><li v-for="occurrenceId in review.occurrenceIds" :key="occurrenceId">{{ occurrenceId }}</li></ul>
            </details>
            <Badge v-if="inspection.review" class="inspector-review-badge" :kind="reviewBadgeKind(inspection.review.state)">{{ inspection.review.label }}</Badge>
            <h2>{{ inspection.name }}</h2>
            <p class="tree-inspector-life-span"><strong>{{ inspection.lifeSpanLabel }}</strong><span v-if="inspection.lifeSpanLabel.includes('?')">granica przybliżona lub brakująca</span></p>
            <p class="inspector-subtitle">{{ inspection.summary }}</p>
            <a v-if="inspection.fullRecordHref" class="open-full-record-link tree-external-profile-link" :href="inspection.fullRecordHref">Otwórz pełną kartę · {{ inspection.name }}</a>
            <OpenFullRecordLink v-else-if="!inspection.suppressFullRecordLink" kind="person" :id="inspection.personId" :label="inspection.name" />
            <section v-if="inspection.importantFacts.length" class="detail-block">
              <h3>Najważniejsze ustalenia</h3>
              <div class="tree-facts"><div v-for="fact in inspection.importantFacts" :key="`${fact.label}-${fact.value}`" class="tree-fact"><span>{{ fact.label }}</span><strong>{{ fact.value }}</strong><small v-if="fact.place">{{ fact.place }}</small><EntityLink v-if="fact.sourceId" class="text-action" kind="source" :id="fact.sourceId" label="Pokaż źródło" /></div></div>
            </section>
            <p v-else class="tree-inspector-empty">Brak potwierdzonych faktów do krótkiego podsumowania.</p>
          </section>

          <section v-else-if="activeTab === 'family'" class="tree-inspector-pane">
            <h2>Rodzina</h2>
            <p v-if="!inspection.family.parents.length && !inspection.family.coParents.length && !inspection.family.children.length && !inspection.family.siblings.length && !inspection.unions.length && !inspection.sourceRelations.length" class="tree-inspector-empty is-important">Brak relacji rodzinnych w tym zakresie. To luka do triage kuratora.</p>
            <section v-if="inspection.family.parents.length" class="tree-family-group">
              <h3>Rodzice</h3>
              <article v-for="member in inspection.family.parents" :key="member.id" class="tree-family-member">
                <div><span>{{ member.relationLabel }}</span><strong>{{ member.personName }}</strong><small>{{ member.lifeSpanLabel }}</small></div>
                <Badge :kind="member.identityScope === 'hypothesis_group' ? 'is-warning' : undefined">{{ member.statusLabel }}</Badge>
                <p v-if="member.identityNotice" class="tree-family-identity-notice">{{ member.identityNotice }}</p>
                <div class="tree-family-actions"><button @click="member.focus()">Pokaż w drzewie</button><EntityLink kind="person" :id="member.personId" :label="`Profil: ${member.personName}`" /></div>
                <EntityLink v-for="source in member.sources" :key="source.id" class="text-action" kind="source" :id="source.id" :label="`Źródło: ${source.title}`" />
              </article>
            </section>
            <section v-if="inspection.unions.length" class="tree-family-group">
              <h3>Partnerzy i małżeństwa</h3>
              <div class="union-list"><article v-for="union in inspection.unions" :key="union.id" class="union-card" :class="[union.statusClass, { 'is-active-union': union.isActive }]">
              <div class="union-card-heading"><button class="union-partner" @click="union.focus()">{{ union.partnerName }}</button><Badge :kind="union.statusKind">{{ union.status }}</Badge></div>
              <p>{{ union.details }}</p><button v-if="union.canChoose" class="union-tree-action" :disabled="union.isActive" @click="union.choose()">{{ union.isActive ? "Widoczne w drzewie" : "Pokaż tylko to małżeństwo" }}</button>
              <EntityLink v-for="source in union.sources" :key="source.id" class="text-action" kind="source" :id="source.id" :label="`Źródło: ${source.title}`" />
              </article></div>
            </section>
            <section v-if="inspection.family.coParents.length" class="tree-family-group">
              <h3>Współrodzice dzieci</h3>
              <p class="inspector-subtitle">Wspólne rodzicielstwo nie jest automatycznie dowodem małżeństwa.</p>
              <article v-for="member in inspection.family.coParents" :key="member.id" class="tree-family-member">
                <div><span>{{ member.relationLabel }}</span><strong>{{ member.personName }}</strong><small>{{ member.lifeSpanLabel }}</small></div>
                <Badge :kind="member.identityScope === 'hypothesis_group' ? 'is-warning' : undefined">{{ member.statusLabel }}</Badge>
                <p v-if="member.identityNotice" class="tree-family-identity-notice">{{ member.identityNotice }}</p>
                <div class="tree-family-actions"><button @click="member.focus()">Pokaż w drzewie</button><EntityLink kind="person" :id="member.personId" :label="`Profil: ${member.personName}`" /></div>
              </article>
            </section>
            <section v-if="inspection.family.children.length" class="tree-family-group">
              <h3>Dzieci</h3>
              <article v-for="member in inspection.family.children" :key="member.id" class="tree-family-member">
                <div><span>{{ member.relationLabel }}</span><strong>{{ member.personName }}</strong><small>{{ member.lifeSpanLabel }}</small></div>
                <Badge :kind="member.identityScope === 'hypothesis_group' ? 'is-warning' : undefined">{{ member.statusLabel }}</Badge>
                <p v-if="member.identityNotice" class="tree-family-identity-notice">{{ member.identityNotice }}</p>
                <div class="tree-family-actions"><button @click="member.focus()">Pokaż w drzewie</button><EntityLink kind="person" :id="member.personId" :label="`Profil: ${member.personName}`" /></div>
                <EntityLink v-for="source in member.sources" :key="source.id" class="text-action" kind="source" :id="source.id" :label="`Źródło: ${source.title}`" />
              </article>
            </section>
            <section v-if="inspection.family.siblings.length" class="tree-family-group">
              <h3>Rodzeństwo</h3>
              <article v-for="member in inspection.family.siblings" :key="member.id" class="tree-family-member">
                <div><span>{{ member.relationLabel }}</span><strong>{{ member.personName }}</strong><small>{{ member.lifeSpanLabel }}</small></div>
                <Badge :kind="member.identityScope === 'hypothesis_group' ? 'is-warning' : undefined">{{ member.statusLabel }}</Badge>
                <p v-if="member.identityNotice" class="tree-family-identity-notice">{{ member.identityNotice }}</p>
                <div class="tree-family-actions"><button @click="member.focus()">Pokaż w drzewie</button><EntityLink kind="person" :id="member.personId" :label="`Profil: ${member.personName}`" /></div>
                <EntityLink v-for="source in member.sources" :key="source.id" class="text-action" kind="source" :id="source.id" :label="`Źródło: ${source.title}`" />
              </article>
            </section>
            <section v-if="inspection.sourceRelations.length" class="tree-source-relations">
              <h3>Rodzina wskazana w aktach</h3>
              <p class="inspector-subtitle">Relacja jawnie wskazana w źródle lub roli uczestnika nie jest jeszcze automatycznie relacją kanoniczną.</p>
              <article v-for="relation in inspection.sourceRelations" :key="relation.id" :class="{ 'is-unresolved': !relation.personIdentityResolved }">
                <span>{{ relation.relationLabel }}</span><strong>{{ relation.personName }}</strong><small>{{ relation.lifeSpanLabel }}</small>
                <p class="tree-source-relation-status"><strong>{{ relation.resolutionLabel }}</strong></p>
                <div><EntityLink v-if="relation.personId" kind="person" :id="relation.personId" :label="`Profil: ${relation.personName}`" /></div>
                <ul class="tree-source-relation-evidence">
                  <li v-for="source in relation.sources" :key="source.id">
                    <strong>{{ source.sourceTitle }}</strong>
                    <small>{{ source.statuses.map(statusLabel).join(" · ") }}<template v-if="source.readingCertainties.length"> · odczyt {{ source.readingCertainties.map(statusLabel).join(" · ") }}</template></small>
                    <small v-if="source.provenance.includes('event_participation_role')">Podstawa: jawne role uczestników tego aktu</small>
                    <p v-for="literal in source.literals" :key="literal">„{{ literal }}”</p>
                    <EntityLink v-if="source.sourceId" kind="source" :id="source.sourceId" :label="source.sourceTitle" :source-id="source.sourceId" />
                  </li>
                </ul>
              </article>
            </section>
          </section>

          <section v-else-if="activeTab === 'acts'" class="tree-inspector-pane">
            <h2>Akty i materiały</h2>
            <p class="record-summary">{{ inspection.appearanceSummary }} · {{ inspection.uniqueSources.length }} unikalnych źródeł</p>
            <div v-if="inspection.uniqueSources.length" class="tree-source-groups"><details v-for="source in inspection.uniqueSources" :key="source.sourceId" class="tree-source-group">
              <summary><span><strong>{{ source.shortLabel }}</strong><small v-if="source.roles.length">{{ source.roles.join(" · ") }}</small></span><Badge v-if="source.review" :kind="reviewBadgeKind(source.review.state)">{{ source.review.label }}</Badge></summary>
              <p>{{ source.title }}</p><small>{{ source.occurrenceIds.length }} wystąpień · {{ source.eventIds.length }} zdarzeń</small>
              <EntityLink class="tree-record-button" kind="source" :id="source.sourceId" :label="source.title">Otwórz akt i transkrypcję</EntityLink>
            </details></div>
            <p v-else class="tree-inspector-empty">Brak materiałów w bezpiecznym eksporcie.</p>
            <div v-if="inspection.unavailableCount" class="restricted-note">Część materiału rodzinnego nie występuje w bezpiecznym eksporcie UI.</div>
            <p v-if="inspection.materialNote" class="inspector-subtitle">{{ inspection.materialNote }}</p>
          </section>

          <section v-else class="tree-inspector-pane">
            <h2>Badania</h2><p class="inspector-subtitle">Tropy i nierozstrzygnięte dane nie są przedstawiane jako fakty.</p>
            <div v-if="inspection.candidates.length" class="tree-record-list"><EntityLink v-for="candidate in inspection.candidates" :key="candidate.id" class="tree-record-button tree-candidate-button" kind="source" :id="candidate.sourceId" :label="candidate.sourceTitle">
              <span class="tree-record-heading"><Badge kind="is-conflict">nierozstrzygnięte</Badge><span class="tree-record-count">{{ candidate.priorityLabel }}</span></span>
              <strong>{{ candidate.occurrenceLabel }}</strong><span class="tree-record-roles">{{ candidate.rolesLabel }}</span><p>{{ candidate.basis }}</p>
              <ul v-if="candidate.support.length" class="candidate-evidence-list is-support"><li v-for="item in candidate.support" :key="item">Za: {{ item }}</li></ul>
              <ul v-if="candidate.against.length" class="candidate-evidence-list is-against"><li v-for="item in candidate.against" :key="item">Przeciw/ograniczenie: {{ item }}</li></ul>
              <ul v-if="candidate.humanReviewDebt.length" class="candidate-evidence-list is-debt"><li v-for="item in candidate.humanReviewDebt" :key="item">Do Twojej kontroli: {{ item }}</li></ul>
              <small v-if="candidate.scoreSemantics === 'research_priority_not_probability'">Kolejność kontroli, nie procent pewności.</small>
            </EntityLink></div>
            <div v-for="alert in inspection.alerts.filter((item) => item.kind === 'unresolved_fact')" :key="alert.id" class="restricted-note">{{ alert.label }}: {{ alert.detail }}</div>
          </section>

          <section v-if="inspection.structureActions.length" class="tree-inspector-structure" aria-label="Sterowanie widokiem drzewa"><h3>Widok drzewa</h3><div class="structure-actions"><button v-for="action in inspection.structureActions" :key="action.label" class="structure-action" @click="action.run()">{{ action.label }}</button></div></section>
        </div>
      </div>
    </div>
  </Teleport>
</template>
