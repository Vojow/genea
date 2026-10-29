<script setup lang="ts">
withDefaults(defineProps<{ kind?: string }>(), { kind: "" });
</script>

<template>
  <span class="badge" :class="kind"><slot /></span>
</template>
