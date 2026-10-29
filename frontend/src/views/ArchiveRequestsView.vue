<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { RouterLink } from "vue-router";
import { appUrl } from "../base-path";
import PrivateRouteGate from "../components/PrivateRouteGate.vue";
import WorkspaceHeader from "../components/WorkspaceHeader.vue";
import { entityLocation } from "../router";
import { useAppStore } from "../stores/app";
import {
  ARCHIVE_REQUEST_READINESS, ARCHIVE_REQUEST_STATUSES, archiveRequestMatches,
  parseArchiveRequests, safeArchiveUrl, safeSourceId, type ArchiveRequest,
} from "./archiveRequests";

const store = useAppStore();
const requests = shallowRef<ArchiveRequest[] | null>(null);
const loading = ref(false);
const error = ref("");
const query = ref("");
const status = ref<ArchiveRequest["status"] | "">("");
const selectedId = ref("");
const copyMessage = ref("");
const copying = ref(false);
const unlocked = computed(() => store.curatorAccess.available && store.curatorAccess.authenticated);
const filtered = computed(() => (requests.value || []).filter((item) => (!status.value || item.status === status.value) && archiveRequestMatches(item, query.value)));
const selected = computed(() => filtered.value.find((item) => item.id === selectedId.value) || filtered.value[0] || null);
const unsentCount = computed(() => (requests.value || []).filter((item) => item.status === "draft" || item.status === "ready").length);
let activeRequest: AbortController | null = null;
let sessionGeneration = 0;
let strictDeadline: number | null = null;
let strictExpiryTimer: ReturnType<typeof setTimeout> | null = null;

function clearPrivateState(): void {
  sessionGeneration += 1;
  activeRequest?.abort();
  activeRequest = null;
  if (strictExpiryTimer !== null) clearTimeout(strictExpiryTimer);
  strictExpiryTimer = null;
  strictDeadline = null;
  requests.value = null;
  loading.value = false;
  error.value = "";
  selectedId.value = "";
  query.value = "";
  status.value = "";
  copyMessage.value = "";
  copying.value = false;
}

async function load(): Promise<void> {
  clearPrivateState();
  if (!unlocked.value) return;
  const request = new AbortController();
  // Start before the network request so transport time cannot extend elevation.
  const requestStartedAt = Date.now();
  activeRequest = request;
  loading.value = true;
  try {
    const response = await fetch(appUrl("/api/private/archive-requests"), {
      method: "GET", cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" }, signal: request.signal,
    });
    if (!response.ok) throw new Error("Archive requests unavailable");
    const envelope: unknown = await response.json();
    const result = parseArchiveRequests(envelope);
    if (activeRequest === request && !request.signal.aborted && unlocked.value) {
      const expiresIn = (envelope as { strictExpiresIn?: unknown }).strictExpiresIn;
      const deadline = typeof expiresIn === "number" && Number.isFinite(expiresIn) && expiresIn > 0
        ? requestStartedAt + Math.min(expiresIn * 1000, 2_147_483_647)
        : null;
      const allowStrict = store.curatorAccess.strict && deadline !== null && deadline > Date.now();
      requests.value = result.filter((item) => item.privacy !== "strict_private" || allowStrict);
      if (allowStrict && requests.value.some((item) => item.privacy === "strict_private")) {
        strictDeadline = deadline;
        strictExpiryTimer = setTimeout(() => { void load(); }, deadline! - Date.now());
      }
    }
  } catch {
    if (activeRequest === request && !request.signal.aborted) {
      requests.value = null;
      error.value = "Nie udało się pobrać kwerend. Wymagane jest aktywne prywatne API z obsługą kwerend; sprawdź także sesję dostępu.";
    }
  } finally {
    if (activeRequest === request) {
      activeRequest = null;
      loading.value = false;
    }
  }
}

function bodyActionAllowed(): boolean {
  // Timers may be delayed in background tabs; action handlers check the clock too.
  if (strictDeadline !== null && Date.now() >= strictDeadline) {
    void load();
    return false;
  }
  return unlocked.value && (selected.value?.privacy !== "strict_private"
    || (store.curatorAccess.strict && strictDeadline !== null));
}

async function copyBody(): Promise<void> {
  if (!bodyActionAllowed()) return;
  const item = selected.value;
  if (!item || !unlocked.value) return;
  const generation = sessionGeneration;
  copying.value = true;
  copyMessage.value = "";
  try {
    await navigator.clipboard.writeText(item.requestBody);
    if (generation === sessionGeneration && selected.value?.id === item.id) copyMessage.value = "Skopiowano treść kwerendy.";
  } catch {
    if (generation === sessionGeneration && selected.value?.id === item.id) copyMessage.value = "Nie udało się skopiować. Zaznacz treść kwerendy i skopiuj ją ręcznie.";
  } finally {
    if (generation === sessionGeneration) copying.value = false;
  }
}

function downloadBody(): void {
  if (!bodyActionAllowed()) return;
  const item = selected.value;
  if (!item || !unlocked.value) return;
  try {
    const url = URL.createObjectURL(new Blob([item.requestBody], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    try {
      link.href = url;
      link.download = `kwerenda-${item.id.replace(/[^a-zA-Z0-9_-]/g, "_")}.txt`;
      document.body.append(link);
      link.click();
    } finally {
      link.remove();
      URL.revokeObjectURL(url);
    }
    copyMessage.value = "Przygotowano plik tekstowy do pobrania.";
  } catch {
    copyMessage.value = "Nie udało się przygotować pliku. Możesz skopiować treść kwerendy.";
  }
}

watch(() => selected.value?.id, () => { copyMessage.value = ""; });
watch([
  () => store.curatorAccess.available, () => store.curatorAccess.authenticated,
  () => store.curatorAccess.strict, () => store.curatorAccess.csrf,
], () => { void load(); }, { immediate: true, flush: "sync" });
onBeforeUnmount(clearPrivateState);
</script>

<template>
  <section class="archive-requests-view">
    <WorkspaceHeader eyebrow="Badania · prywatne" title="Kwerendy do instytucji" description="Kwerendy ze wszystkich spraw i instytucji. Przeglądaj zakres, uzupełnienia i treść przygotowaną do ręcznego wysłania." />
    <p v-if="!store.curatorAccess.available" class="empty-state-card" role="status">Kwerendy są niedostępne w podglądzie statycznym lub przy wyłączonym prywatnym API. Otwórz aplikację na serwerze z aktywnym prywatnym API.</p>
    <PrivateRouteGate v-else-if="!store.curatorAccess.authenticated" kind-label="Kwerendy do instytucji" />
    <template v-else>
      <div class="archive-toolbar"><p>Treść można skopiować lub pobrać; wysyłka odbywa się ręcznie poza Genea.</p><button type="button" :disabled="loading" @click="load">Odśwież kwerendy</button></div>
      <p v-if="loading" role="status">Wczytywanie prywatnych kwerend…</p>
      <p v-else-if="error" class="empty-state-card" role="alert">{{ error }}</p>
      <template v-else-if="requests">
        <p class="archive-counts" role="status">Wszystkie: {{ requests.length }} · Nie wysłano: {{ unsentCount }} · Wyniki: {{ filtered.length }}</p>
        <div class="archive-filters">
          <label for="archive-query">Szukaj we wszystkich kwerendach<input id="archive-query" v-model="query" type="search" placeholder="Sprawa, instytucja, sygnatura lub treść" autocomplete="off" /></label>
          <label for="archive-status">Status<select id="archive-status" v-model="status"><option value="">Wszystkie statusy</option><option v-for="(label, value) in ARCHIVE_REQUEST_STATUSES" :key="value" :value="value">{{ label }}</option></select></label>
        </div>
        <p v-if="!requests.length" class="empty-state-card">Brak kwerend w prywatnym rejestrze.</p>
        <p v-else-if="!filtered.length" class="empty-state-card">Brak kwerend pasujących do filtrów. Zmień wyszukiwanie lub status.</p>
        <div v-else class="archive-layout">
          <ul class="archive-list" aria-label="Kwerendy do instytucji">
            <li v-for="item in filtered" :key="item.id">
              <button type="button" class="archive-card" :aria-pressed="selected?.id === item.id" aria-controls="archive-request-detail" :data-request-id="item.id" @click="selectedId = item.id">
                <strong>{{ item.title || item.id }}</strong><span>{{ item.repository || 'Instytucja niepodana' }}</span>
                <span>Sprawa: {{ item.caseId || 'Bez przypisanej sprawy' }} · Priorytet: {{ item.priority }}</span>
                <span>{{ ARCHIVE_REQUEST_STATUSES[item.status] }}<b v-if="item.status === 'draft' || item.status === 'ready'"> · Nie wysłano</b></span>
                <small>{{ ARCHIVE_REQUEST_READINESS[item.readiness] }}</small>
              </button>
            </li>
          </ul>
          <article v-if="selected" id="archive-request-detail" class="archive-detail" aria-labelledby="archive-detail-title" :data-request-id="selected.id">
            <header><p class="eyebrow">{{ ARCHIVE_REQUEST_STATUSES[selected.status] }}</p><h2 id="archive-detail-title">{{ selected.title || selected.id }}</h2><p v-if="selected.status === 'draft' || selected.status === 'ready'" class="archive-unsent"><strong>Nie wysłano</strong></p></header>
            <dl class="archive-metadata">
              <div><dt>Instytucja</dt><dd>{{ selected.repository || 'Nie podano' }}</dd></div>
              <div><dt>Adresat</dt><dd>{{ selected.recipient || 'Nie podano' }}</dd></div>
              <div><dt>Sprawa</dt><dd><RouterLink v-if="selected.caseId" :to="entityLocation('case', selected.caseId)">{{ selected.caseId }}</RouterLink><span v-else>Bez przypisanej sprawy</span></dd></div>
              <div><dt>Gotowość</dt><dd>{{ ARCHIVE_REQUEST_READINESS[selected.readiness] }}</dd></div>
              <div><dt>Priorytet w rejestrze</dt><dd>{{ selected.priority }}</dd></div>
              <div><dt>Prywatność według rejestru</dt><dd>{{ selected.privacy || 'Nie podano' }}</dd></div>
            </dl>
            <section class="archive-section" aria-labelledby="archive-missing-title"><h3 id="archive-missing-title">Dane do uzupełnienia</h3><ul v-if="selected.missingFields.length"><li v-for="(field, i) in selected.missingFields" :key="i">{{ field }}</li></ul><p v-else>Rejestr nie wskazuje brakujących pól.</p></section>
            <section class="archive-section" aria-labelledby="archive-targets-title"><h3 id="archive-targets-title">Materiały objęte kwerendą</h3><ul v-if="selected.targets.length" class="archive-targets"><li v-for="(target, i) in selected.targets" :key="i"><strong>{{ target.title || 'Materiał bez tytułu' }}</strong><p>Sygnatura: {{ target.signature || 'Nie podano' }} · Daty: {{ target.dates || 'Nie podano' }}</p><RouterLink v-if="safeSourceId(target.sourceId)" :to="entityLocation('source', target.sourceId)">Otwórz akt / źródło · {{ target.sourceId }}</RouterLink><a v-if="safeArchiveUrl(target.url)" :href="safeArchiveUrl(target.url)!" target="_blank" rel="noopener noreferrer">Katalog instytucji (nowa karta)</a></li></ul><p v-else>Nie wskazano materiałów.</p></section>
            <section class="archive-section" aria-labelledby="archive-body-title"><h3 id="archive-body-title">Treść kwerendy</h3><p v-if="!selected.requestBody">Nie przygotowano treści kwerendy.</p><template v-else><div class="archive-actions"><button type="button" :disabled="copying" @click="copyBody">{{ copying ? 'Kopiowanie…' : 'Kopiuj treść' }}</button><button type="button" @click="downloadBody">Pobierz .txt</button></div><p v-if="copyMessage" role="status">{{ copyMessage }}</p><div class="archive-body archive-prose" tabindex="0" role="region" aria-label="Pełna treść kwerendy">{{ selected.requestBody }}</div></template></section>
            <section class="archive-section" aria-labelledby="archive-instructions-title"><h3 id="archive-instructions-title">Instrukcja dalszych kroków</h3><p class="archive-prose">{{ selected.instructions || 'Nie podano instrukcji.' }}</p></section>
            <section v-if="selected.responseNote" class="archive-section"><h3>Notatka o odpowiedzi</h3><p class="archive-prose">{{ selected.responseNote }}</p></section>
            <details class="archive-section"><summary>Pochodzenie i identyfikatory</summary><ul v-if="selected.provenance.length"><li v-for="(entry, i) in selected.provenance" :key="i">{{ entry }}</li></ul><p v-else>Nie podano pochodzenia.</p><dl class="archive-metadata"><div><dt>ID kwerendy</dt><dd>{{ selected.id }}</dd></div><div><dt>Utworzono</dt><dd>{{ selected.createdAt || 'Nie podano' }}</dd></div><div><dt>Zaktualizowano</dt><dd>{{ selected.updatedAt || 'Nie podano' }}</dd></div></dl></details>
          </article>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.archive-requests-view { min-width: 0; max-width: 1600px; margin-inline: auto; color: var(--ink); }
.archive-toolbar, .archive-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.archive-toolbar { justify-content: space-between; margin-bottom: 18px; }
.archive-toolbar p { flex: 1 1 260px; }
.archive-requests-view button, .archive-requests-view input, .archive-requests-view select { font: inherit; }
.archive-requests-view button { color: var(--forest, #275647); background: var(--paper, #fffdf8); border: 1px solid var(--line, #ccc); border-radius: 8px; padding: 10px 14px; cursor: pointer; }
.archive-requests-view button:disabled { opacity: .6; cursor: wait; }
.archive-requests-view :is(button, input, select, a, summary, [tabindex]):focus-visible { outline: 3px solid var(--forest, #275647); outline-offset: 3px; }
.archive-counts { font-weight: 600; }
.archive-filters { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 16px; margin: 18px 0 24px; }
.archive-filters label { display: grid; gap: 8px; font-weight: 600; }
.archive-filters input, .archive-filters select { min-width: 0; width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid var(--line, #ccc); border-radius: 8px; background: var(--paper, #fffdf8); color: inherit; }
.archive-layout { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(0, 2fr); align-items: start; gap: 24px; }
.archive-list { display: grid; gap: 12px; list-style: none; margin: 0; padding: 0; min-width: 0; }
.archive-list .archive-card { display: grid; gap: 8px; width: 100%; text-align: left; color: inherit; overflow-wrap: anywhere; }
.archive-card[aria-pressed="true"] { border-color: var(--forest, #275647); box-shadow: inset 4px 0 var(--forest, #275647); }
.archive-card strong { font-size: 17px; }
.archive-card small { font-size: 13px; line-height: 1.5; }
.archive-detail { padding: clamp(16px, 3vw, 28px); background: var(--paper, #fffdf8); border: 1px solid var(--line, #ccc); border-radius: 12px; min-width: 0; overflow-wrap: anywhere; }
.archive-detail h2 { margin-top: 8px; font-size: 26px; line-height: 1.3; }
.archive-detail h3 { font-size: 18px; margin: 0 0 12px; }
.archive-detail p, .archive-detail li { line-height: 1.65; }
.archive-unsent { color: var(--forest, #275647); }
.archive-metadata { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 20px 0; }
.archive-metadata dt { font-weight: 700; }
.archive-metadata dd { margin: 5px 0 0; line-height: 1.6; }
.archive-section { border-top: 1px solid var(--line, #ccc); padding-top: 20px; margin-top: 20px; }
.archive-section ul { padding-left: 22px; }
.archive-targets li + li { margin-top: 18px; }
.archive-targets p { margin: 4px 0; }
.archive-targets a { display: block; width: fit-content; margin-top: 6px; }
.archive-prose { white-space: pre-wrap; line-height: 1.8; }
.archive-body { margin-top: 16px; padding: 16px; border: 1px solid var(--line, #ccc); border-radius: 8px; background: var(--canvas, #f8f5ee); user-select: text; }
.archive-detail summary { cursor: pointer; font-weight: 700; }
@media (max-width: 900px) { .archive-layout { grid-template-columns: minmax(0, 1fr); } .archive-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .archive-filters, .archive-list, .archive-metadata { grid-template-columns: minmax(0, 1fr); } .archive-detail h2 { font-size: 22px; } }
</style>
