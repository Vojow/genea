<script setup lang="ts">
import { computed } from "vue";

import { ROUTE_NAMES } from "../router";
import type { EntityKind } from "../router";
import { collectionLabel, collectionLocation } from "../navigation/catalog";


const props = defineProps<{ kind?: EntityKind | null; label?: string | null; workspace: string; privateLocked?: boolean }>();
const workspaceLocation = computed(() => props.workspace === "Katalog"
  ? { name: ROUTE_NAMES.catalog }
  : props.workspace.startsWith("Badania")
    ? { name: ROUTE_NAMES.researchDashboard }
    : props.workspace === "Rodzina"
      ? { name: ROUTE_NAMES.tree }
      : props.workspace === "Mapa"
        ? { name: ROUTE_NAMES.map }
        : props.workspace === "Sieć FAN"
          ? { name: ROUTE_NAMES.graph }
          : props.workspace === "Źródła"
            ? { name: ROUTE_NAMES.sources }
            : null);
</script>

<template>
  <nav class="entity-breadcrumbs" aria-label="Okruszki">
    <RouterLink to="/tree">Genea</RouterLink><span aria-hidden="true">/</span>
    <RouterLink v-if="workspaceLocation" :to="workspaceLocation">{{ workspace }}</RouterLink><span v-else>{{ workspace }}</span>
    <template v-if="kind"><span aria-hidden="true">/</span><RouterLink :to="collectionLocation(kind)">{{ collectionLabel(kind) }}</RouterLink></template>
    <template v-if="label"><span aria-hidden="true">/</span><strong>{{ privateLocked ? "Obiekt chroniony" : label }}</strong></template>
  </nav>
</template>
