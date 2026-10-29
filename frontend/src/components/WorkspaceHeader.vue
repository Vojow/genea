<script setup lang="ts">
withDefaults(defineProps<{
  eyebrow: string;
  title: string;
  description?: string;
}>(), { description: "" });
</script>

<template>
  <header class="workspace-page-header">
    <div class="workspace-page-copy">
      <p class="eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="description" class="lead">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="workspace-page-actions"><slot name="actions" /></div>
  </header>
</template>
