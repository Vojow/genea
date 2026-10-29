<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import {
  fieldAssetUrl,
  freezeField,
  loadReviewSession,
  newCommandId,
  revealField,
  ReviewApiError,
  sha256Literal,
  submitField,
  type ReviewField,
  type ReviewScale,
  type ReviewSession,
} from "./api";
import { loadLocalReadings, saveLocalReading } from "./local-readings";

const scaleLabels: Record<ReviewScale, string> = {
  field: "wycinek pola",
  context: "szerszy kontekst",
  fullAct: "pełny akt",
};

const kindLabels: Record<string, string> = {
  "person-name": "imię lub nazwisko",
  place: "miejsce",
  date: "data lub czas",
  age: "wiek",
  occupation: "zawód lub status",
  relationship: "relacja",
  number: "liczba",
  other: "fragment tekstu",
};

const scaleOrder: ReviewScale[] = ["field", "context", "fullAct"];
const session = ref<ReviewSession | null>(null);
const loading = ref(true);
const locked = ref(false);
const errorMessage = ref("");
const readings = reactive<Record<string, Partial<Record<ReviewScale, string>>>>({});
const pendingFields = reactive(new Set<string>());
const fieldErrors = reactive<Record<string, string>>({});

function runIdFromPath(pathname = window.location.pathname): string | null {
  const match = pathname.match(/^\/blind-review\/([^/]+)\/?$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

const expectedRunId = computed(() => runIdFromPath());

function reading(field: ReviewField, scale = field.visibleScale): string {
  return readings[field.id]?.[scale] || "";
}

function kindLabel(kind: string): string {
  return kindLabels[kind] || kindLabels.other;
}

function setReading(field: ReviewField, value: string): void {
  if (!session.value || field.frozenScales.includes(field.visibleScale)) return;
  readings[field.id] = { ...readings[field.id], [field.visibleScale]: value };
  saveLocalReading(session.value.runId, field.id, field.visibleScale, value);
}

function nextScale(field: ReviewField): ReviewScale | null {
  const currentIndex = scaleOrder.indexOf(field.visibleScale);
  return scaleOrder[currentIndex + 1] || null;
}

function currentFrozen(field: ReviewField): boolean {
  return field.frozenScales.includes(field.visibleScale);
}

function currentSubmitted(field: ReviewField): boolean {
  return field.submittedScales.includes(field.visibleScale);
}

function hasLocalReading(field: ReviewField): boolean {
  return Boolean(reading(field).trim());
}

function canReveal(field: ReviewField): boolean {
  return currentFrozen(field) && currentSubmitted(field) && nextScale(field) !== null;
}

function updateField(fieldId: string, update: (field: ReviewField) => ReviewField): void {
  if (!session.value) return;
  session.value = {
    ...session.value,
    fields: session.value.fields.map((field) => field.id === fieldId ? update(field) : field),
  };
}

function handleActionError(fieldId: string, error: unknown): void {
  if (error instanceof ReviewApiError && error.status === 423) locked.value = true;
  fieldErrors[fieldId] = error instanceof Error ? error.message : "Nie udało się wykonać operacji.";
}

async function freeze(field: ReviewField): Promise<void> {
  if (!session.value || currentSubmitted(field) || pendingFields.has(field.id)) return;
  const literal = reading(field);
  if (!literal.trim()) {
    fieldErrors[field.id] = "Wpisz literalny odczyt przed zamrożeniem.";
    return;
  }
  pendingFields.add(field.id);
  fieldErrors[field.id] = "";
  try {
    if (!currentFrozen(field)) {
      const submissionSha256 = await sha256Literal(literal);
      await freezeField(field.id, field.visibleScale, submissionSha256, session.value.csrfToken, newCommandId());
      updateField(field.id, (current) => ({
        ...current,
        frozenScales: [...new Set([...current.frozenScales, current.visibleScale])],
      }));
    }
    await submitField(field.id, field.visibleScale, literal, session.value.csrfToken, newCommandId());
    updateField(field.id, (current) => ({
      ...current,
      submittedScales: [...new Set([...current.submittedScales, current.visibleScale])],
    }));
  } catch (error) {
    handleActionError(field.id, error);
  } finally {
    pendingFields.delete(field.id);
  }
}

async function reveal(field: ReviewField): Promise<void> {
  if (!session.value || !canReveal(field) || pendingFields.has(field.id)) return;
  const targetScale = nextScale(field);
  if (!targetScale) return;
  pendingFields.add(field.id);
  fieldErrors[field.id] = "";
  try {
    await revealField(field.id, targetScale, session.value.csrfToken, newCommandId());
    updateField(field.id, (current) => ({
      ...current,
      visibleScale: targetScale,
      availableScales: [...new Set([...current.availableScales, targetScale])],
    }));
  } catch (error) {
    handleActionError(field.id, error);
  } finally {
    pendingFields.delete(field.id);
  }
}

async function initialise(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  locked.value = false;
  const routeRunId = expectedRunId.value;
  if (!routeRunId) {
    errorMessage.value = "Nieprawidłowy adres sesji odczytu.";
    loading.value = false;
    return;
  }
  try {
    const result = await loadReviewSession();
    if (result.runId !== routeRunId) {
      locked.value = true;
      errorMessage.value = "Identyfikator sesji nie zgadza się z adresem. Dostęp został zablokowany.";
      return;
    }
    session.value = result;
    Object.assign(readings, loadLocalReadings(result.runId));
  } catch (error) {
    if (error instanceof ReviewApiError && error.status === 423) locked.value = true;
    errorMessage.value = error instanceof Error ? error.message : "Nie udało się załadować sesji odczytu.";
  } finally {
    loading.value = false;
  }
}

onMounted(initialise);
</script>

<template>
  <main class="blind-shell">
    <header class="blind-header">
      <p class="eyebrow">Niezależny odczyt źródła</p>
      <h1>Odczytaj dokładnie to, co widzisz</h1>
      <p class="intro">
        Zacznij od małego wycinka. Kontekst i pełny akt zostaną odsłonięte dopiero po zamrożeniu wcześniejszego odczytu.
      </p>
    </header>

    <section class="privacy-note" aria-labelledby="local-data-title">
      <div aria-hidden="true" class="privacy-icon">✓</div>
      <div>
        <h2 id="local-data-title">Hash i propozycja są rozdzielone</h2>
        <p>
          Najpierw serwer utrwala wyłącznie hash SHA-256. Następnie literalny tekst trafia osobnym żądaniem jako propozycja odczytu — nie jako fakt — i nigdy nie wraca w danych sesji.
        </p>
      </div>
    </section>

    <section v-if="loading" class="status-panel" role="status" aria-live="polite">
      <span class="spinner" aria-hidden="true"></span>
      Ładowanie bezpiecznej sesji…
    </section>

    <section v-else-if="locked" class="status-panel status-panel--locked" role="alert">
      <h2>Sesja zablokowana</h2>
      <p>{{ errorMessage || "Nie można kontynuować tego odczytu." }}</p>
    </section>

    <section v-else-if="errorMessage" class="status-panel status-panel--error" role="alert">
      <h2>Nie udało się otworzyć sesji</h2>
      <p>{{ errorMessage }}</p>
      <button type="button" class="secondary-button" @click="initialise">Spróbuj ponownie</button>
    </section>

    <section v-else-if="session" class="review-list" aria-label="Pola do niezależnego odczytu">
      <p class="run-summary">Sesja {{ session.runId }} · pól: {{ session.fields.length }}</p>

      <article v-for="(field, index) in session.fields" :key="field.id" class="review-card">
        <header class="card-header">
          <div>
            <p class="field-number">Pole {{ index + 1 }} z {{ session.fields.length }}</p>
            <h2>{{ kindLabel(field.kind) }}</h2>
          </div>
          <span class="stage-pill">{{ scaleLabels[field.visibleScale] }}</span>
        </header>

        <ol class="stage-list" aria-label="Postęp odsłaniania">
          <li
            v-for="scale in scaleOrder"
            :key="scale"
            :class="{
              'is-visible': scale === field.visibleScale,
              'is-frozen': field.submittedScales.includes(scale),
            }"
          >
            <span aria-hidden="true">{{ field.submittedScales.includes(scale) ? "✓" : "" }}</span>
            {{ scaleLabels[scale] }}
          </li>
        </ol>

        <figure class="source-frame">
          <img
            :src="fieldAssetUrl(field.id, field.visibleScale)"
            :alt="`Obraz do odczytu: ${scaleLabels[field.visibleScale]}`"
          />
          <figcaption>Widoczna skala: {{ scaleLabels[field.visibleScale] }}</figcaption>
        </figure>

        <div class="reading-form">
          <label :for="`reading-${index}`">Literalny odczyt z widocznego obrazu</label>
          <textarea
            :id="`reading-${index}`"
            :value="reading(field)"
            :disabled="currentFrozen(field) || pendingFields.has(field.id) || locked"
            rows="5"
            spellcheck="false"
            autocomplete="off"
            placeholder="Przepisz znaki bez normalizacji i bez dopowiadania z kontekstu…"
            @input="setReading(field, ($event.target as HTMLTextAreaElement).value)"
          ></textarea>
          <p v-if="currentSubmitted(field)" class="frozen-note" role="status">
            Odczyt tej skali został zamrożony i przekazany jako propozycja. W audycie ujawnień pozostaje jego hash.
          </p>
          <p v-else-if="currentFrozen(field) && hasLocalReading(field)" class="frozen-note" role="status">
            Hash jest już zamrożony. Ponów przekazanie tej samej lokalnej treści, aby zakończyć etap.
          </p>
          <p v-if="currentFrozen(field) && !currentSubmitted(field) && !hasLocalReading(field)" class="field-error" role="alert">
            Brakuje lokalnej treści odpowiadającej zamrożonemu hashowi. Tego etapu nie można bezpiecznie wznowić w tej sesji przeglądarki.
          </p>
          <p v-if="fieldErrors[field.id]" class="field-error" role="alert">{{ fieldErrors[field.id] }}</p>

          <div class="actions">
            <button
              v-if="!currentSubmitted(field)"
              type="button"
              class="primary-button"
              :disabled="pendingFields.has(field.id) || locked || (currentFrozen(field) && !hasLocalReading(field))"
              @click="freeze(field)"
            >
              {{ pendingFields.has(field.id) ? "Zapisywanie…" : currentFrozen(field) ? "Przekaż zamrożony odczyt" : field.visibleScale === "fullAct" ? "Opcjonalnie zamroź pełny akt" : "Zamroź ten odczyt" }}
            </button>
            <button
              v-if="canReveal(field)"
              type="button"
              class="secondary-button"
              :disabled="pendingFields.has(field.id) || locked"
              @click="reveal(field)"
            >
              {{ pendingFields.has(field.id) ? "Odsłanianie…" : `Odsłoń: ${scaleLabels[nextScale(field)!]}` }}
            </button>
          </div>
        </div>
      </article>

      <p v-if="session.fields.length === 0" class="status-panel" role="status">
        W tej sesji nie ma pól oczekujących na odczyt.
      </p>
    </section>
  </main>
</template>
