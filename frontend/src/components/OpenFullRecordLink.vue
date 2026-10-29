<script setup lang="ts">
import type { EntityKind } from "../router";
import EntityLink from "./EntityLink.vue";

defineProps<{ kind: EntityKind; id: string; label: string }>();
</script>

<template>
  <EntityLink class="open-full-record-link" :kind="kind" :id="id" :label="label">Otwórz pełną kartę · {{ label }}</EntityLink>
</template>
