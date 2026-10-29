<script setup lang="ts">
import { useAppStore } from "../stores/app";
import EntityLink from "./EntityLink.vue";

const store = useAppStore();
</script>

<template>
  <section v-if="store.recentEntities.length" class="recent-entities" aria-labelledby="recent-title">
    <h2 id="recent-title">Ostatnio oglądane</h2>
    <div>
      <EntityLink v-for="item in store.recentEntities" :key="`${item.kind}:${item.id}`" :kind="item.kind" :id="item.id" :label="item.label" />
    </div>
  </section>
</template>
