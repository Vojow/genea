<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { CuratorAccess, IdentityReviewCandidate } from "../types/presentation";

interface PreviewSide {
  occurrenceId: string;
  personId?: string | null;
  label: string;
  rawName: string;
  sourceId: string;
  sourceTitle: string;
}

interface IdentityPreview {
  id: string;
  status: string;
  expectedStatus: string;
  fingerprint: string;
  score: number;
  threshold: number;
  scoreSemantics: string;
  basis: string;
  from: PreviewSide;
  to: PreviewSide;
  canonicalOptions: Array<{ personId: string; label: string; mergeBlockers: string[] }>;
  requiresCanonicalChoice: boolean;
  requiresPreferredName: boolean;
  impact: Array<{
    personId: string;
    occurrences: number;
    names: number;
    households: number;
    protectedDependencies: string[];
  }>;
  blockers: string[];
  canResolve: boolean;
}

const props = defineProps<{
  candidate: IdentityReviewCandidate | null;
  access: CuratorAccess;
}>();
const emit = defineEmits<{ close: []; resolved: [] }>();

const preview = ref<IdentityPreview | null>(null);
const decision = ref<"same" | "different" | "">("");
const canonicalPersonId = ref("");
const preferredName = ref("");
const rationale = ref("");
const busy = ref(false);
const message = ref("");
const pendingCommandId = ref("");

function newCommandId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `identity-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const readyToSubmit = computed(() => {
  if (!preview.value?.canResolve || !props.access.strict || !decision.value) return false;
  if (rationale.value.trim().length < 20) return false;
  if (decision.value === "same" && preview.value.requiresCanonicalChoice && !canonicalPersonId.value) return false;
  if (decision.value === "same" && preview.value.requiresCanonicalChoice) {
    const selected = preview.value.canonicalOptions.find((item) => item.personId === canonicalPersonId.value);
    if (selected?.mergeBlockers.length) return false;
  }
  if (decision.value === "same" && preview.value.requiresPreferredName && !preferredName.value.trim()) return false;
  return true;
});

async function loadPreview(): Promise<void> {
  preview.value = null;
  message.value = "";
  decision.value = "";
  canonicalPersonId.value = "";
  rationale.value = "";
  pendingCommandId.value = newCommandId();
  if (!props.candidate || !props.access.authenticated) return;
  busy.value = true;
  try {
    const response = await fetch(`/api/private/identity-candidate/${encodeURIComponent(props.candidate.id)}`, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error("Nie udało się odświeżyć podglądu kandydatury.");
    preview.value = await response.json() as IdentityPreview;
    preferredName.value = preview.value.from.label || preview.value.to.label;
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie udało się pobrać podglądu.";
  } finally {
    busy.value = false;
  }
}

async function submit(): Promise<void> {
  if (!preview.value || !decision.value || !readyToSubmit.value) return;
  busy.value = true;
  message.value = "";
  try {
    const response = await fetch(`/api/private/identity-candidate/${encodeURIComponent(preview.value.id)}/resolve`, {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": props.access.csrf,
      },
      body: JSON.stringify({
        decision: decision.value,
        expectedStatus: preview.value.expectedStatus,
        expectedFingerprint: preview.value.fingerprint,
        commandId: pendingCommandId.value,
        canonicalPersonId: canonicalPersonId.value || null,
        preferredName: preferredName.value.trim() || null,
        rationale: rationale.value.trim(),
        actor: "użytkownik + kurator UI",
      }),
    });
    const result = await response.json() as { detail?: string };
    if (!response.ok) throw new Error(result.detail || "Zapis decyzji nie powiódł się.");
    message.value = "Decyzja została zapisana wraz z audytem. Odświeżam graf…";
    emit("resolved");
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Zapis decyzji nie powiódł się.";
  } finally {
    busy.value = false;
  }
}

watch(
  () => [props.candidate?.id, props.access.authenticated, props.access.strict],
  () => { void loadPreview(); },
  { immediate: true },
);
</script>

<template>
  <div v-if="candidate" class="identity-dialog-backdrop" @click.self="emit('close')">
    <section class="identity-dialog" role="dialog" aria-modal="true" aria-labelledby="identity-dialog-title">
      <header>
        <div>
          <p class="identity-kicker">KURACJA TOŻSAMOŚCI</p>
          <h2 id="identity-dialog-title">Porównaj i rozstrzygnij</h2>
        </div>
        <button type="button" aria-label="Zamknij" @click="emit('close')">×</button>
      </header>
      <p class="identity-warning">Wynik jest priorytetem badawczym, nie prawdopodobieństwem. Żadna decyzja nie jest podejmowana automatycznie.</p>
      <p v-if="message" class="identity-message" role="status">{{ message }}</p>
      <div v-if="!access.authenticated" class="identity-gate">
        Odblokuj najpierw lokalną sesję przyciskiem „Odblokuj dane prywatne”.
      </div>
      <div v-else-if="busy && !preview" class="identity-gate">Odświeżam stan kandydatury…</div>
      <template v-else-if="preview">
        <div class="identity-score">
          <strong>{{ Math.round(preview.score * 100) }}/100</strong>
          <span>próg kolejki {{ Math.round(preview.threshold * 100) }}/100</span>
        </div>
        <div class="identity-compare">
          <article>
            <h3>{{ preview.from.label }}</h3>
            <p lang="ru">oryg.: {{ preview.from.rawName }}</p>
            <small>{{ preview.from.sourceTitle }}</small>
          </article>
          <span aria-hidden="true">⇄</span>
          <article>
            <h3>{{ preview.to.label }}</h3>
            <p lang="ru">oryg.: {{ preview.to.rawName }}</p>
            <small>{{ preview.to.sourceTitle }}</small>
          </article>
        </div>
        <div class="identity-basis"><strong>Podstawa kandydatury</strong><p>{{ preview.basis }}</p></div>
        <div v-if="preview.impact.length" class="identity-impact">
          <strong>Podgląd skutków</strong>
          <p v-for="item in preview.impact" :key="item.personId">
            {{ item.personId }}: {{ item.occurrences }} wystąpień, {{ item.names }} nazw, {{ item.households }} gospodarstw
          </p>
        </div>
        <div v-if="preview.blockers.length" class="identity-blockers" role="alert">
          Co najmniej jeden profil ma chronione zależności: {{ preview.blockers.join(", ") }}. Możesz wybrać go jako profil kanoniczny, wybrać „Różne osoby” albo użyć szerszego manifestu kuratorskiego.
        </div>
        <form class="identity-form" @submit.prevent="submit">
          <fieldset>
            <legend>Decyzja kuratora</legend>
            <label><input v-model="decision" type="radio" value="same" /> Ta sama osoba</label>
            <label><input v-model="decision" type="radio" value="different" /> Różne osoby</label>
          </fieldset>
          <fieldset v-if="decision === 'same' && preview.requiresCanonicalChoice">
            <legend>Który profil pozostaje kanoniczny?</legend>
            <label v-for="option in preview.canonicalOptions" :key="option.personId">
              <input v-model="canonicalPersonId" type="radio" :value="option.personId" />
              {{ option.label }} <small>{{ option.personId }}<span v-if="option.mergeBlockers.length"> · blokery scalenia drugiego profilu: {{ option.mergeBlockers.join(", ") }}</span></small>
            </label>
          </fieldset>
          <label v-if="decision === 'same' && preview.requiresPreferredName">
            Preferowana nazwa nowej osoby
            <input v-model="preferredName" autocomplete="off" required />
          </label>
          <label>
            Uzasadnienie decyzji
            <textarea v-model="rationale" rows="4" minlength="20" required placeholder="Wskaż konkretne akty, zgodności i konflikty…"></textarea>
          </label>
          <small v-if="!access.strict">Przed zapisem ponownie potwierdź hasło w panelu danych prywatnych, aby aktywować zakres ścisły.</small>
          <button type="submit" :disabled="busy || !readyToSubmit">Zapisz decyzję i audyt</button>
        </form>
      </template>
    </section>
  </div>
</template>

<style scoped>
.identity-dialog-backdrop { position: fixed; inset: 0; z-index: 110; display: grid; place-items: center; padding: 1rem; background: #14231f8c; }
.identity-dialog { width: min(760px, 100%); max-height: calc(100vh - 2rem); overflow: auto; padding: 1.25rem; border: 1px solid #c9bdab; border-radius: 18px; background: #fbf8f1; color: #292b28; box-shadow: 0 24px 70px #14231f59; }
.identity-dialog header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.identity-dialog header button { min-width: 44px; min-height: 44px; border: 1px solid #c9bdab; border-radius: 9px; background: white; font-size: 1.5rem; }
.identity-kicker { margin: 0 0 .25rem; color: #9a3f24; font-size: .72rem; font-weight: 800; letter-spacing: .12em; }
.identity-dialog h2 { margin: 0; }
.identity-warning, .identity-message, .identity-gate, .identity-blockers { padding: .75rem; border-radius: 10px; line-height: 1.45; }
.identity-warning { background: #fff0cf; color: #6f5420; }
.identity-message { background: #e4eee9; }
.identity-gate, .identity-blockers { background: #f6dfd7; color: #742e1d; }
.identity-score { display: flex; align-items: baseline; gap: .75rem; margin: 1rem 0; }
.identity-score strong { font-size: 1.45rem; color: #795c26; }
.identity-score span { color: #68665f; font-size: .85rem; }
.identity-compare { display: grid; grid-template-columns: 1fr auto 1fr; align-items: stretch; gap: .75rem; }
.identity-compare article { padding: 1rem; border: 1px solid #d9d0c2; border-radius: 12px; background: white; }
.identity-compare h3 { margin: 0 0 .5rem; }
.identity-compare p { margin: 0 0 .5rem; color: #5f635e; }
.identity-compare > span { align-self: center; color: #9a3f24; font-size: 1.4rem; }
.identity-basis, .identity-impact { margin-top: 1rem; padding: 1rem; border-top: 1px solid #d9d0c2; }
.identity-basis p, .identity-impact p { margin: .35rem 0 0; }
.identity-form { display: grid; gap: 1rem; margin-top: 1rem; }
.identity-form fieldset { display: grid; gap: .65rem; border: 1px solid #d9d0c2; border-radius: 10px; padding: .85rem; }
.identity-form label { display: grid; gap: .4rem; }
.identity-form fieldset label { display: flex; align-items: center; gap: .55rem; min-height: 44px; }
.identity-form input:not([type="radio"]), .identity-form textarea, .identity-form button { min-height: 44px; border: 1px solid #c9bdab; border-radius: 9px; padding: .65rem .75rem; font: inherit; }
.identity-form button { border-color: #275f54; background: #275f54; color: white; font-weight: 700; }
.identity-form button:disabled { opacity: .5; }
@media (max-width: 640px) { .identity-dialog-backdrop { padding: .5rem; } .identity-dialog { max-height: calc(100vh - 1rem); } .identity-compare { grid-template-columns: 1fr; } .identity-compare > span { justify-self: center; transform: rotate(90deg); } }
</style>
