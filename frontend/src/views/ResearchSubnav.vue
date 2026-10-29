<script setup lang="ts">
import UiIcon from "../components/UiIcon.vue";

const items = [
  { to: "/research/dashboard", label: "Pulpit", icon: "research" },
  { to: "/research/cases", label: "Sprawy", icon: "case" },
  { to: "/research/questions", label: "Pytania", icon: "question" },
  { to: "/research/volumes", label: "Woluminy i AI", icon: "volume" },
  { to: "/research/evidence", label: "Dowody", icon: "document" },
];
</script>

<template>
  <nav class="research-subnav" aria-label="Sekcje badań">
    <div class="research-subnav-heading"><span>Warsztat</span><strong>Badania</strong></div>
    <RouterLink v-for="item in items" :key="item.to" :to="item.to"><UiIcon :name="item.icon" /><span>{{ item.label }}</span></RouterLink>
  </nav>
</template>
