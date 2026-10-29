<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { entityLocation, type EntityKind } from "../router";
import { labelForKind } from "../routing/entities";
import { statusLabel } from "../utils/formatting";
import UiIcon from "./UiIcon.vue";
import { useAppStore } from "../stores/app";
import { getActivePinia } from "pinia";


const props = withDefaults(defineProps<{
  kind: EntityKind;
  id: string;
  label: string;
  status?: string | null;
  availability?: "found" | "private" | "not_exported" | "missing";
  occurrenceId?: string | null;
  sourceId?: string | null;
}>(), { status: null, availability: "found", occurrenceId: null, sourceId: null });

const privateLocked = computed(() => props.availability === "private");
const activePinia = getActivePinia();
const store = activePinia ? useAppStore(activePinia) : null;
const targetId = computed(() => {
  if (props.kind !== "person" || !store?.payload) return props.id;
  return store.personContext(props.id)?.publicProfileId || props.id;
});
const visibleLabel = computed(() => privateLocked.value ? `${labelForKind(props.kind)} chroniony` : props.label);
const linked = computed(() => props.availability === "found" || props.availability === "private");
const icons: Record<EntityKind, string> = {
  person: "person", line: "line", place: "place", event: "event", source: "source",
  household: "household", cluster: "cluster", case: "case", question: "question", volume: "volume", run: "run",
};

function captureContext(): void {
  if (props.kind === "person" && !privateLocked.value) {
    store?.activatePerson(props.id, {
      view: store.activeView,
      ...(props.occurrenceId ? { occurrenceId: props.occurrenceId } : {}),
      ...(props.sourceId ? { sourceId: props.sourceId } : {}),
    });
  } else if (store?.activePersonKey && (props.occurrenceId || props.sourceId)) {
    store.activatePerson(store.activePersonKey, {
      view: store.activePersonOrigin?.view || store.activeView,
      ...(props.occurrenceId ? { occurrenceId: props.occurrenceId } : {}),
      ...(props.sourceId ? { sourceId: props.sourceId } : {}),
    });
  }
  document.dispatchEvent(new CustomEvent("genea:capture-navigation-context", {
    detail: { kind: props.kind, id: props.id },
  }));
}
</script>

<template>
  <RouterLink
    v-if="linked"
    class="entity-link"
    :class="`entity-link-${kind}`"
    :to="entityLocation(kind, targetId)"
    :aria-label="`${labelForKind(kind)}: ${visibleLabel}`"
    @click="captureContext"
  >
    <slot>
      <span class="entity-link-icon" aria-hidden="true"><UiIcon :name="icons[kind]" /></span>
      <span class="entity-link-label">{{ visibleLabel }}</span>
      <small v-if="status && !privateLocked" class="entity-link-status">{{ statusLabel(status) }}</small>
    </slot>
  </RouterLink>
  <span v-else class="entity-link entity-link-disabled" :title="availability === 'not_exported' ? 'Obiekt nie został dopuszczony do eksportu' : 'Obiekt nie istnieje w tym eksporcie'">
    <span class="entity-link-icon" aria-hidden="true"><UiIcon :name="icons[kind]" /></span>
    <span class="entity-link-label">{{ labelForKind(kind) }}</span>
    <small class="entity-link-status">{{ availability === "not_exported" ? "poza eksportem" : "brak referencji" }}</small>
  </span>
</template>
