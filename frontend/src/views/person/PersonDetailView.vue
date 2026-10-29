<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import type { EntityReference } from "../../routing/entities";
import { EntityIndex } from "../../routing/entities";
import { useAppStore } from "../../stores/app";
import { projectPersonProfile } from "../../models/person-profile-projector";
import type { PresentedPersonContext } from "../../types/presentation";
import EntityBreadcrumbs from "../../components/EntityBreadcrumbs.vue";
import PresentedNameReading from "../../components/PresentedNameReading.vue";
import PersonProfileHeader from "./PersonProfileHeader.vue";
import PersonFactSummary from "./PersonFactSummary.vue";
import PersonFamilyPanel from "./PersonFamilyPanel.vue";
import PersonTimeline from "./PersonTimeline.vue";
import PersonResearchState from "./PersonResearchState.vue";
import PersonOccurrenceList from "./PersonOccurrenceList.vue";
import EntityLink from "../../components/EntityLink.vue";
import { externalNetworkReturnLocation } from "../../router";


const props = defineProps<{ entity: EntityReference }>();
const store = useAppStore();
const route = useRoute();
const externalNetworkReturn = computed(() => externalNetworkReturnLocation(route.query));
const index = computed(() => new EntityIndex(store.payload!));
const context = computed<PresentedPersonContext>(() => {
  const resolved = index.value.personContexts.resolveAny(String(props.entity.raw?.personKey || props.entity.id));
  if (resolved) return resolved;
  const rawOccurrenceIds = Array.isArray(props.entity.raw?.occurrenceIds) ? props.entity.raw.occurrenceIds.map(String) : [];
  const occurrenceIds = [...new Set([
    ...rawOccurrenceIds,
    ...(store.payload?.occurrences || []).filter((item) => String((item as Record<string, unknown>).personId || "") === props.entity.id).map((item) => String((item as Record<string, unknown>).id || "")),
  ].filter(Boolean))];
  const treePeople = Array.isArray(store.payload?.familyTree?.people) ? store.payload.familyTree.people as Array<Record<string, unknown>> : [];
  const treePersonIds = treePeople.filter((person) => {
    const ids = Array.isArray(person.occurrenceIds) ? person.occurrenceIds.map(String) : [];
    return String(person.id || "") === props.entity.id || ids.some((id) => occurrenceIds.includes(id));
  }).map((person) => String(person.id));
  return {
    personKey: props.entity.id,
    publicProfileId: props.entity.id,
    identityScope: "canonical",
    canonicalPersonIds: [props.entity.id],
    treePersonIds,
    mapPersonIds: [props.entity.id],
    graphIdentityIds: [props.entity.id],
    occurrenceIds,
    sourceIds: [],
    aliases: [props.entity.id],
    label: props.entity.label,
    status: props.entity.status,
  };
});
const baseProfile = computed(() => store.payload?.personProfiles?.find((item) => item.personKey === context.value?.personKey) || null);
const model = computed(() => context.value ? projectPersonProfile(store.payload!, context.value, baseProfile.value, props.entity.raw || {}) : null);

function openPerspective(view: "tree" | "graph" | "map"): void {
  if (!context.value) return;
  store.activatePerson(context.value, { view: store.activePersonOrigin?.view || store.activeView });
  store.captureNavigationContext();
}

function remember(): void {
  if (!model.value || props.entity.raw?.private) return;
  store.recordRecent({ kind: "person", id: model.value.publicProfileId, label: model.value.displayName, personKey: model.value.personKey });
}

onMounted(remember);
watch(() => props.entity.id, remember);
</script>

<template>
  <article v-if="model && context" class="entity-detail-view person-detail-view">
    <EntityBreadcrumbs workspace="Rodzina" kind="person" :label="model.displayName" />
    <RouterLink v-if="externalNetworkReturn" class="quiet-button person-external-network-return" :to="externalNetworkReturn">← Wróć do sieci kandydackiej</RouterLink>
    <PersonProfileHeader
      :model="model"
      :can-open-tree="Boolean(context.treePersonIds.length)"
      :can-open-graph="Boolean(context.graphIdentityIds.length || context.occurrenceIds.length)"
      :can-open-map="Boolean(context.mapPersonIds.length)"
      @navigate="openPerspective"
    />
    <p class="person-profile-method-note">{{ model.methodNote }} <span>Źródło nie jest dowodem.</span></p>
    <div v-if="model.familyKnown" class="person-profile-warning person-family-known-notice" role="status"><strong>{{ model.familyKnown.label }}</strong><span>{{ model.familyKnown.notice }}</span></div>
    <div v-if="model.identityNotice" class="person-profile-warning" role="alert"><strong>{{ model.identityLabel }}</strong><span>{{ model.identityNotice }}</span></div>
    <details v-for="review in model.localIdentityReviews" :key="review.decisionTier" class="person-local-identity-review" :open="review.humanReviewOpen">
      <summary>{{ review.humanReviewLabel }}</summary>
      <strong>{{ review.label }}</strong>
      <p>Ta decyzja dotyczy wyłącznie jawnie oznaczonych wystąpień. Nie zmienia osoby kanonicznej ani relacji.</p>
      <ul :aria-label="`Backing occurrences: ${review.humanReviewLabel}`">
        <li v-for="occurrenceId in review.occurrenceIds" :key="occurrenceId">{{ occurrenceId }}</li>
      </ul>
    </details>
    <section v-if="model.sourceReportedMemberships.length" class="person-identity-components" aria-labelledby="person-external-memberships-title">
      <header><p class="eyebrow">CZŁONKOSTWA SOURCE-REPORTED</p><h2 id="person-external-memberships-title">Role w zewnętrznych drzewach</h2></header>
      <p class="person-family-identity-note">Każde drzewo pozostaje osobnym, zależnym źródłem. Powtórzenie tej samej relacji w dwóch sieciach nie jest przedstawiane jako potwierdzenie.</p>
      <div>
        <article v-for="membership in model.sourceReportedMemberships" :key="membership.id">
          <div>
            <strong>{{ membership.networkLabel }}</strong>
            <span>{{ membership.roleLabel }}</span>
            <small v-if="membership.anchorState === 'visible' && membership.anchorLabel">Punkt odniesienia: {{ membership.anchorLabel }}</small>
            <small v-else-if="membership.anchorState === 'masked'">Punkt odniesienia ukryty przez reguły prywatności</small>
            <small v-else>Brak widocznego punktu odniesienia</small>
            <ul v-if="membership.classificationState === 'conflict'" aria-label="Wszystkie konkurencyjne klasyfikacje">
              <li v-for="(alternative, index) in membership.alternatives" :key="`${alternative.role}:${index}`">{{ alternative.roleLabel }} · ścieżka {{ alternative.pathLength }} {{ alternative.pathLength === 1 ? "relacji" : "relacji" }}</li>
            </ul>
          </div>
          <span>imported_unverified</span>
        </article>
      </div>
    </section>
    <section v-if="model.identityScope === 'hypothesis_group'" class="person-identity-components" aria-labelledby="person-identity-components-title">
      <header><p class="eyebrow">SKŁADNIKI TOŻSAMOŚCI</p><h2 id="person-identity-components-title">Oddzielne osoby wymagające korelacji</h2></header>
      <div><article v-for="component in model.identityComponents" :key="component.id"><EntityLink kind="person" :id="component.id" :label="component.label" /><span>{{ component.occurrenceIds.length }} {{ component.occurrenceIds.length === 1 ? "wystąpienie" : "wystąpienia" }}</span></article><article v-if="model.unassignedOccurrenceCount" class="is-unassigned"><strong>Wystąpienia jeszcze nieprzypisane</strong><span>{{ model.unassignedOccurrenceCount }}</span></article></div>
    </section>
    <div class="person-profile-layout">
      <div class="person-profile-primary">
        <PersonFactSummary :facts="model.facts" />
        <PersonFamilyPanel :family="model.family" :identity-ambiguous="model.identityScope === 'hypothesis_group' || model.treeEvidenceStatus === 'unresolved'" />
        <PersonResearchState :questions="model.questions" :conflicts="model.conflicts" :candidates="model.identityCandidates" :unresolved-facts="model.unresolvedFacts" />
      </div>
      <div class="person-profile-evidence">
        <PersonTimeline :entries="model.timeline" :undated="model.undatedTimeline" :identity-ambiguous="model.identityScope === 'hypothesis_group' || model.treeEvidenceStatus === 'unresolved'" :identity-group="model.identityScope === 'hypothesis_group'" :life-span-label="model.lifeSpanLabel" :life-span-notice="model.lifeSpanNotice" />
        <section v-if="model.nameReadings.length" class="person-profile-section person-name-readings" aria-labelledby="person-names-title"><header class="detail-section-heading"><p class="eyebrow">NAZWY ŹRÓDŁOWE</p><h2 id="person-names-title">{{ model.identityScope === 'hypothesis_group' ? 'Jak zapisano kandydatów' : model.treeEvidenceStatus === 'unresolved' ? 'Jak zapisano tę pozycję w źródłach' : 'Jak zapisano tę osobę' }}</h2></header><p class="name-method-note">Oryginał, transliteracja i polska forma pozostają osobnymi warstwami.</p><PresentedNameReading v-for="reading in model.nameReadings.slice(0, 3)" :key="reading.id" :reading="reading" /><details v-if="model.nameReadings.length > 3" class="person-name-readings-more"><summary>Pokaż dalsze warianty zapisu <small>{{ model.nameReadings.length - 3 }}</small></summary><PresentedNameReading v-for="reading in model.nameReadings.slice(3)" :key="reading.id" :reading="reading" /></details></section>
        <PersonOccurrenceList :entries="model.occurrenceEntries" :person-id="model.publicProfileId" :show-components="model.identityScope === 'hypothesis_group'" />
        <p v-if="model.approvedActMaterialSummary" class="name-method-note" data-testid="approved-act-material-summary">{{ model.approvedActMaterialSummary }}</p>
        <section v-if="model.sourceReportedLeadEntries.length" class="person-profile-section" aria-labelledby="person-reported-leads-title">
          <header class="detail-section-heading"><p class="eyebrow">MATERIAŁ ZGŁOSZONY</p><h2 id="person-reported-leads-title">Profil zgłoszony w drzewie</h2></header>
          <p class="name-method-note">Wpis z drzewa pozostaje osobnym materiałem. Licznik aktów obejmuje powiązane dokumenty źródłowe.</p>
          <article v-for="entry in model.sourceReportedLeadEntries" :key="entry.id">
            <strong>{{ entry.rawName || entry.normalizedName || "Nazwa niewskazana" }}</strong>
            <p>Wpis zgłoszony w drzewie zewnętrznym</p>
          </article>
        </section>
      </div>
    </div>
  </article>
</template>
