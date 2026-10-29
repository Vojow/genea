<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { appUrl } from "../base-path";
import { useAppStore } from "../stores/app";
import UiIcon from "./UiIcon.vue";

interface PrivateStatus {
  enabled: boolean;
  authenticated: boolean;
  strict: boolean;
  expiresIn: number;
  locked: boolean;
  csrf?: string | null;
}

const store = useAppStore();

interface PrivateField {
  field_name: string;
  value: unknown;
  classification: string;
  provenance_kind: string;
}

interface PrivateProfile {
  id: string;
  fields: PrivateField[];
  notes: Array<{ id: string; note_text: string; informant: string; classification: string }>;
  relationships: Array<{
    id: string;
    relationship_type: string;
    subject_person_id: string;
    object_person_id: string;
    verification_status: string;
  }>;
  attachments: Array<{ id: string; media_type: string; classification: string }>;
  strictIncluded: boolean;
}

interface PrivatePersonListItem {
  id: string;
  preferredName: string;
  classification: string;
}

interface PrivateTreeOverlayPerson {
  treePersonId: string;
  name: string;
  summary: string;
  sex?: string | null;
  privacy: "private_revealed";
  evidenceStatus: "family_known" | "private_unverified";
}

interface PrivateTreeOverlay {
  people: PrivateTreeOverlayPerson[];
  mappedCount: number;
  unmappedCount: number;
  totalPrivatePeople: number;
}

const available = ref(false);
const open = ref(false);
const status = ref<PrivateStatus | null>(null);
const password = ref("");
const csrf = ref("");
const personId = ref("");
const privatePeople = ref<PrivatePersonListItem[]>([]);
const treeOverlay = ref<PrivateTreeOverlay | null>(null);
const showOnTree = ref(true);
const newPersonName = ref("");
const newPersonInformant = ref("");
const profile = ref<PrivateProfile | null>(null);
const message = ref("");
const busy = ref(false);
let expiryTimer: number | null = null;

function publishTreeOverlay(): void {
  document.dispatchEvent(new CustomEvent("genea:private-tree-overlay", {
    detail: showOnTree.value ? treeOverlay.value : null,
  }));
}

function clearPrivateState(): void {
  profile.value = null;
  privatePeople.value = [];
  treeOverlay.value = null;
  csrf.value = "";
  store.setCuratorAccess({ authenticated: false, strict: false, csrf: "" });
  store.setPrivatePeople([]);
  publishTreeOverlay();
}

function scheduleExpiry(): void {
  if (expiryTimer !== null) window.clearTimeout(expiryTimer);
  expiryTimer = null;
  if (!status.value?.authenticated || status.value.expiresIn <= 0) return;
  expiryTimer = window.setTimeout(() => {
    clearPrivateState();
    void refreshStatus();
  }, (status.value.expiresIn + 1) * 1000);
}

const buttonLabel = computed(() => status.value?.authenticated ? "Dane prywatne" : "Odblokuj dane prywatne");

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(appUrl(path), {
    ...options,
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(csrf.value ? { "X-CSRF-Token": csrf.value } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) throw new Error(response.status === 423 ? "Sesja prywatna jest zablokowana." : "Operacja prywatna nie powiodła się.");
  return (await response.json()) as T;
}

async function refreshStatus(): Promise<void> {
  try {
    status.value = await request<PrivateStatus>("/api/private/status");
    available.value = status.value.enabled;
    if (status.value.authenticated && status.value.csrf) csrf.value = status.value.csrf;
    store.setCuratorAccess({
      available: status.value.enabled,
      authenticated: status.value.authenticated,
      strict: status.value.strict,
      csrf: csrf.value,
    });
    if (!status.value.authenticated) {
      clearPrivateState();
    }
    scheduleExpiry();
  } catch {
    available.value = false;
    store.setCuratorAccess({ available: false, authenticated: false, strict: false, csrf: "" });
  }
}

async function unlock(strict = false): Promise<void> {
  busy.value = true;
  message.value = "";
  try {
    if (strict) {
      await request<{ strict: boolean }>("/api/private/strict-unlock", {
        method: "POST",
        body: JSON.stringify({ password: password.value }),
      });
    } else {
      const result = await request<{ authenticated: boolean; csrf: string }>("/api/private/unlock", {
        method: "POST",
        body: JSON.stringify({ password: password.value }),
      });
      csrf.value = result.csrf;
    }
    password.value = "";
    await refreshStatus();
    await loadPeople();
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie udało się odblokować danych.";
  } finally {
    password.value = "";
    busy.value = false;
  }
}

async function loadPeople(): Promise<void> {
  if (!status.value?.authenticated) return;
  const [result, overlay] = await Promise.all([
    request<{ people: PrivatePersonListItem[] }>("/api/private/persons"),
    request<PrivateTreeOverlay>("/api/private/tree-overlay"),
  ]);
  privatePeople.value = result.people;
  store.setPrivatePeople(result.people);
  treeOverlay.value = overlay;
  publishTreeOverlay();
}

async function loadProfile(): Promise<void> {
  if (!personId.value.trim()) return;
  busy.value = true;
  message.value = "";
  try {
    profile.value = await request<PrivateProfile>(`/api/private/person/${encodeURIComponent(personId.value.trim())}`);
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie znaleziono profilu.";
    profile.value = null;
  } finally {
    busy.value = false;
  }
}

async function createPerson(): Promise<void> {
  busy.value = true;
  message.value = "";
  try {
    const created = await request<{ id: string }>("/api/private/person", {
      method: "POST",
      body: JSON.stringify({
        commandId: typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `private-person-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        preferredName: newPersonName.value,
        informant: newPersonInformant.value || null,
        classification: "family_private",
        provenanceKind: "family_testimony",
        consentStatus: "not_asked",
      }),
    });
    personId.value = created.id;
    newPersonName.value = "";
    newPersonInformant.value = "";
    await loadPeople();
    await loadProfile();
  } catch (error) {
    message.value = error instanceof Error ? error.message : "Nie udało się utworzyć osoby.";
  } finally {
    busy.value = false;
  }
}

async function endSession(lockAll: boolean): Promise<void> {
  busy.value = true;
  try {
    await request(lockAll ? "/api/private/lock" : "/api/private/logout", {
      method: "POST",
      body: "{}",
    });
  } finally {
    clearPrivateState();
    busy.value = false;
    await refreshStatus();
  }
}

async function openAttachment(id: string): Promise<void> {
  const response = await fetch(appUrl(`/api/private/attachment/${encodeURIComponent(id)}`), {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) {
    message.value = "Nie można otworzyć prywatnego załącznika.";
    return;
  }
  const url = URL.createObjectURL(await response.blob());
  window.open(url, "_blank", "noopener,noreferrer");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function refreshWhenVisible(): void {
  if (document.visibilityState === "visible") {
    void refreshStatus().then(() => loadPeople());
  }
}

onMounted(() => {
  void refreshStatus().then(() => loadPeople());
  document.addEventListener("visibilitychange", refreshWhenVisible);
});
watch(showOnTree, publishTreeOverlay);
onUnmounted(() => {
  if (expiryTimer !== null) window.clearTimeout(expiryTimer);
  clearPrivateState();
  document.removeEventListener("visibilitychange", refreshWhenVisible);
});
</script>

<template>
  <button v-if="available" class="private-access-trigger" type="button" :aria-label="buttonLabel" @click="open = true">
    <UiIcon name="lock" /><span class="private-trigger-long">{{ buttonLabel }}</span><span class="private-trigger-short">Prywatne</span>
  </button>
  <div v-if="available && open" class="private-access-backdrop" @click.self="open = false">
    <section class="private-access-panel" role="dialog" aria-modal="true" aria-labelledby="private-access-title">
      <header>
        <div>
          <p class="private-kicker">ZASZYFROWANA SESJA</p>
          <h2 id="private-access-title">Dane prywatne rodziny</h2>
        </div>
        <button type="button" aria-label="Zamknij" @click="open = false">×</button>
      </header>
      <p v-if="message" class="private-message" role="alert">{{ message }}</p>
      <form v-if="!status?.authenticated" class="private-login" @submit.prevent="unlock(false)">
        <label for="private-password">Hasło prywatnego UI</label>
        <input id="private-password" v-model="password" type="password" autocomplete="current-password" required />
        <button type="submit" :disabled="busy">Odblokuj sesję</button>
        <small>Hasło nie jest zapisywane w przeglądarce ani przesyłane w adresie URL.</small>
      </form>
      <template v-else>
        <div class="private-session-state">
          <span>Sesja aktywna · {{ status.expiresIn }} s</span>
          <span>{{ status.strict ? "zakres ścisły aktywny" : "zakres rodzinny" }}</span>
        </div>
        <label class="private-tree-toggle">
          <input v-model="showOnTree" type="checkbox" />
          Pokazuj odblokowane dane na drzewie
        </label>
        <small v-if="treeOverlay" class="private-tree-summary">
          {{ treeOverlay.mappedCount }} z {{ treeOverlay.totalPrivatePeople }} osób ma przypisaną kartę drzewa<span v-if="treeOverlay.unmappedCount">; pozostałe są dostępne na liście prywatnej</span>.
        </small>
        <form class="private-profile-search" @submit.prevent="loadProfile">
          <label for="private-person-id">Osoba prywatna</label>
          <select id="private-person-id" v-model="personId" required>
            <option value="" disabled>Wybierz osobę…</option>
            <option v-for="person in privatePeople" :key="person.id" :value="person.id">
              {{ person.preferredName }}
            </option>
          </select>
          <button type="submit" :disabled="busy">Otwórz profil</button>
        </form>
        <details class="private-create-person">
          <summary>Dodaj osobę na podstawie wiedzy rodzinnej</summary>
          <form @submit.prevent="createPerson">
            <label for="private-new-name">Imię i nazwisko</label>
            <input id="private-new-name" v-model="newPersonName" autocomplete="off" required />
            <label for="private-new-informant">Informator</label>
            <input id="private-new-informant" v-model="newPersonInformant" autocomplete="off" />
            <button type="submit" :disabled="busy">Utwórz prywatną osobę</button>
            <small>Zapis otrzymuje status wiedzy rodzinnej; nie staje się automatycznie faktem potwierdzonym aktem.</small>
          </form>
        </details>
        <form v-if="!status.strict" class="private-strict" @submit.prevent="unlock(true)">
          <label for="private-strict-password">Pokaż dane strict_private</label>
          <input id="private-strict-password" v-model="password" type="password" autocomplete="current-password" required />
          <button type="submit" :disabled="busy">Potwierdź ponownie</button>
        </form>
        <article v-if="profile" class="private-profile">
          <h3>{{ String(profile.fields.find((field) => field.field_name === "preferredName")?.value || profile.id) }}</h3>
          <dl>
            <template v-for="field in profile.fields" :key="field.field_name">
              <dt>{{ field.field_name }}</dt><dd>{{ String(field.value) }}</dd>
            </template>
          </dl>
          <section v-if="profile.notes.length"><h4>Notatki rodzinne</h4><p v-for="note in profile.notes" :key="note.id">{{ note.note_text }} <small>— {{ note.informant }}</small></p></section>
          <section v-if="profile.relationships.length"><h4>Relacje</h4><p v-for="relation in profile.relationships" :key="relation.id">{{ relation.relationship_type }} · {{ relation.subject_person_id }} → {{ relation.object_person_id }} · {{ relation.verification_status }}</p></section>
          <section v-if="profile.attachments.length"><h4>Załączniki</h4><button v-for="attachment in profile.attachments" :key="attachment.id" type="button" @click="openAttachment(attachment.id)">Otwórz {{ attachment.media_type }}</button></section>
        </article>
        <footer>
          <button type="button" :disabled="busy" @click="endSession(false)">Wyloguj</button>
          <button type="button" class="private-lock" :disabled="busy" @click="endSession(true)">Zablokuj wszystkie sesje</button>
        </footer>
      </template>
    </section>
  </div>
</template>

<style scoped>
.private-access-trigger { position: fixed; right: 1rem; bottom: 1rem; z-index: 30; min-height: 44px; display: inline-flex; align-items: center; gap: .45rem; padding: .65rem 1rem; border: 1px solid #2b685c; border-radius: 999px; background: #173f38; color: #fff; box-shadow: 0 8px 28px #173f3833; }
.private-access-trigger .ui-icon { width: 17px; height: 17px; }
.private-access-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: 1rem; background: #14231f80; }
.private-access-panel { width: min(680px, 100%); max-height: calc(100vh - 2rem); overflow: auto; padding: 1.25rem; border: 1px solid #c9bdab; border-radius: 18px; background: #fbf8f1; color: #292b28; box-shadow: 0 24px 70px #14231f59; }
.private-access-panel header, .private-access-panel footer, .private-session-state { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.private-access-panel header > button { min-width: 44px; min-height: 44px; font-size: 1.5rem; }
.private-kicker { margin: 0 0 .25rem; color: #9a3f24; font-size: .72rem; font-weight: 800; letter-spacing: .12em; }
.private-access-panel h2 { margin: 0; }
.private-login, .private-profile-search, .private-strict, .private-create-person form { display: grid; gap: .65rem; margin-top: 1rem; }
.private-create-person { margin-top: 1rem; padding: .75rem; border: 1px solid #d9d0c2; border-radius: 10px; }
.private-access-panel input, .private-access-panel select, .private-access-panel button { min-height: 44px; border: 1px solid #c9bdab; border-radius: 9px; padding: .6rem .75rem; font: inherit; }
.private-access-panel button { cursor: pointer; background: #fff; }
.private-login button, .private-profile-search button { background: #275f54; color: #fff; }
.private-session-state { margin: 1rem 0; padding: .75rem; border-radius: 10px; background: #e4eee9; font-size: .85rem; }
.private-tree-toggle { display: flex; align-items: center; gap: .65rem; min-height: 44px; font-weight: 700; }
.private-tree-toggle input { width: 20px; min-height: 20px; margin: 0; }
.private-tree-summary { display: block; color: #5f635e; }
.private-message { padding: .75rem; border-radius: 8px; background: #f6dfd7; }
.private-profile { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid #d9d0c2; }
.private-profile dl { display: grid; grid-template-columns: minmax(8rem, 1fr) 2fr; gap: .4rem 1rem; }
.private-profile dt { font-weight: 700; }
.private-profile dd { margin: 0; overflow-wrap: anywhere; }
.private-access-panel footer { margin-top: 1.25rem; }
.private-lock { color: #8e2e1b; }
.private-trigger-short { display: none; }
@media (max-width: 760px) { .private-access-trigger { top: auto; right: 12px; bottom: calc(74px + env(safe-area-inset-bottom)); left: auto; z-index: 69; width: 44px; padding: 0; justify-content: center; } .private-trigger-long, .private-trigger-short { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; } }
@media (max-width: 600px) { .private-access-panel { height: calc(100vh - 1rem); max-height: none; border-radius: 14px; } .private-access-backdrop { padding: .5rem; } .private-session-state, .private-access-panel footer { align-items: stretch; flex-direction: column; } }
</style>
