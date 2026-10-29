<script setup lang="ts">
import { ref } from "vue";
import type { EntityKind } from "../router";
import { labelForKind } from "../routing/entities";
import { statusLabel } from "../utils/formatting";
import UiIcon from "./UiIcon.vue";


const props = withDefaults(defineProps<{ kind: EntityKind; label: string; status?: string | null; privateLocked?: boolean }>(), { status: null, privateLocked: false });
const copied = ref(false);

async function copyLink(): Promise<void> {
  await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}${window.location.search}`);
  copied.value = true;
  window.setTimeout(() => { copied.value = false; }, 1500);
}
</script>

<template>
  <header class="entity-page-header">
    <div>
      <p class="eyebrow">{{ labelForKind(kind) }}</p>
      <h1>{{ privateLocked ? "Obiekt chroniony" : label }}</h1>
      <span v-if="status && !privateLocked" class="entity-status" :data-status="status">{{ statusLabel(status) }}</span>
    </div>
    <button type="button" class="quiet-button entity-copy-action" @click="copyLink"><UiIcon name="copy" />{{ copied ? "Skopiowano" : "Kopiuj link" }}</button>
  </header>
</template>
