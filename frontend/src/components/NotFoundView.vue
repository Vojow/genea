<script setup lang="ts">
withDefaults(defineProps<{ reason?: "missing" | "not_exported" | "route" }>(), { reason: "route" });
</script>

<template>
  <section class="not-found-view" role="status">
    <p class="eyebrow">NIE ZNALEZIONO</p>
    <h1>Nie można otworzyć tej karty</h1>
    <p v-if="reason === 'not_exported'">Obiekt istnieje jako referencja, ale nie został dopuszczony do tego eksportu.</p>
    <p v-else-if="reason === 'missing'">Identyfikator nie rozwiązuje się w bieżącym, kontrolowanym eksporcie.</p>
    <p v-else>Adres nie odpowiada żadnej trasie Genea.</p>
    <RouterLink to="/tree" class="primary-action">Wróć do drzewa</RouterLink>
  </section>
</template>
