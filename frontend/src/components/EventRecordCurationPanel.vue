<script setup lang="ts">
import { computed, ref } from "vue";

import type { CuratorAccess } from "../types/presentation";

type Decision = "same_record" | "distinct_records" | "defer";
type Preview = {
  id: string;
  sourceTitle: string;
  eventGroup: string;
  status: string;
  fingerprint: string;
  stale: boolean;
  members: Array<Record<string, unknown>>;
  latestDecision?: { decision: string; rationale: string; revision: number } | null;
  impact: { canonicalEventsChanged: number; sourceRowsDeleted: number; affectedViews: string[] };
  canApply: boolean;
};

const props = defineProps<{
  candidateId: string;
  access: CuratorAccess;
}>();
const emit = defineEmits<{ resolved: [] }>();

const preview = ref<Preview | null>(null);
const decision = ref<Decision | "">("");
const rationale = ref("");
const busy = ref(false);
const message = ref("");
const ready = computed(() => Boolean(
  preview.value?.canApply
  && decision.value
  && rationale.value.trim().length >= 20
  && props.access.authenticated
  && props.access.csrf,
));

function commandId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `event-record-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function loadPreview(): Promise<void> {
  if (!props.access.authenticated) {
    message.value = "Odblokuj prywatne UI, aby zobaczyć świeży podgląd kuratorski.";
    return;
  }
  busy.value = true;
  message.value = "";
  try {
    const response = await fetch(`/api/private/event-record-candidate/${encodeURIComponent(props.candidateId)}`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || body.error || "Nie udało się pobrać podglądu.");
    preview.value = body as Preview;
    rationale.value = preview.value.latestDecision?.rationale || "";
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie udało się pobrać podglądu.";
  } finally {
    busy.value = false;
  }
}

async function submit(): Promise<void> {
  if (!preview.value || !decision.value || !ready.value) return;
  busy.value = true;
  message.value = "";
  try {
    const response = await fetch(`/api/private/event-record-candidate/${encodeURIComponent(props.candidateId)}/resolve`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": props.access.csrf,
      },
      body: JSON.stringify({
        decision: decision.value,
        expectedFingerprint: preview.value.fingerprint,
        actor: "local-ui-curator",
        rationale: rationale.value.trim(),
        commandId: commandId(),
      }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || body.error || "Nie udało się zapisać decyzji.");
    message.value = "Decyzja została zapisana z audytem. Odświeżam eksport prezentacyjny.";
    emit("resolved");
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie udało się zapisać decyzji.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="event-record-curation" aria-label="Kuracja rekordów zdarzenia">
    <button v-if="!preview" type="button" :disabled="busy" @click="loadPreview">
      {{ busy ? "Sprawdzam…" : "Sprawdź i rozstrzygnij rekordy" }}
    </button>
    <p v-if="message" role="status">{{ message }}</p>
    <template v-if="preview">
      <p class="curation-state"><strong>Stan:</strong> {{ preview.status }} · kandydatura {{ preview.id }}</p>
      <p v-if="preview.latestDecision"><strong>Ostatnia decyzja:</strong> {{ preview.latestDecision.decision }} (rewizja {{ preview.latestDecision.revision }})</p>
      <ul>
        <li v-for="member in preview.members" :key="String(member.id)">
          {{ member.event_type }} · {{ member.date_text || member.date_from || "bez daty" }} · {{ member.participant_count }} uczestników
        </li>
      </ul>
      <p class="curation-safety">Decyzja zmieni sposób prezentacji w widoku aktu, osi czasu, profilu i mapie. Nie usuwa ani nie nadpisuje żadnego rekordu źródłowego.</p>
      <form @submit.prevent="submit">
        <fieldset>
          <legend>Decyzja kuratora</legend>
          <label><input v-model="decision" type="radio" value="same_record"> To techniczne zapisy tego samego zdarzenia</label>
          <label><input v-model="decision" type="radio" value="distinct_records"> To różne zdarzenia — nie łącz</label>
          <label><input v-model="decision" type="radio" value="defer"> Odłóż decyzję</label>
        </fieldset>
        <label>Uzasadnienie
          <textarea v-model="rationale" rows="3" minlength="20" placeholder="Wskaż podstawę decyzji i ewentualne wątpliwości." />
        </label>
        <button type="submit" :disabled="busy || !ready">{{ busy ? "Zapisuję…" : "Zapisz decyzję z audytem" }}</button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.event-record-curation { display: grid; gap: .75rem; margin-top: .85rem; padding-top: .85rem; border-top: 1px dashed #cdbfa9; }
.event-record-curation button { min-height: 42px; width: fit-content; border: 1px solid #286458; border-radius: 9px; padding: .55rem .85rem; background: #286458; color: white; font: inherit; font-weight: 700; }
.event-record-curation button:disabled { opacity: .5; }
.event-record-curation p { margin: 0; }
.curation-state { color: #5c5b55; }
.curation-safety { padding: .7rem; border-radius: 8px; background: #edf3ef; color: #31564e; }
.event-record-curation form, .event-record-curation fieldset, .event-record-curation form > label { display: grid; gap: .55rem; }
.event-record-curation fieldset { border: 1px solid #d7cbbb; border-radius: 9px; padding: .75rem; }
.event-record-curation textarea { width: 100%; border: 1px solid #c9bdab; border-radius: 8px; padding: .65rem; font: inherit; }
</style>
