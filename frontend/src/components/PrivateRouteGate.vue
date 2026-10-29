<script setup lang="ts">
defineProps<{ kindLabel: string }>();

function openPrivateAccess(): void {
  document.querySelector<HTMLButtonElement>(".private-access-trigger")?.click();
}
</script>

<template>
  <section class="private-route-gate" role="region" aria-labelledby="private-route-title">
    <span aria-hidden="true">▣</span>
    <p class="eyebrow">TRASA CHRONIONA</p>
    <h1 id="private-route-title">Obiekt chroniony</h1>
    <p>Ta karta wymaga aktywnej sesji prywatnej. Adres i tytuł strony nie ujawniają jej treści.</p>
    <p><strong>Typ:</strong> {{ kindLabel }}</p>
    <button type="button" @click="openPrivateAccess">Odblokuj dane prywatne</button>
  </section>
</template>
