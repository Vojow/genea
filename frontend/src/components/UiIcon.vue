<script setup lang="ts">
defineProps<{ name: string }>();
</script>

<template>
  <svg class="ui-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <g v-if="name === 'tree'">
      <circle cx="12" cy="4" r="2.2" /><circle cx="5" cy="19" r="2.2" /><circle cx="12" cy="19" r="2.2" /><circle cx="19" cy="19" r="2.2" />
      <path d="M12 6.2v5.3M5 16.8v-3.3h14v3.3M12 11.5v5.3" />
    </g>
    <g v-else-if="name === 'map' || name === 'place'">
      <path d="M20 10c0 5.2-8 11-8 11S4 15.2 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.6" />
    </g>
    <g v-else-if="name === 'network' || name === 'cluster'">
      <circle cx="5" cy="7" r="2.2" /><circle cx="19" cy="6" r="2.2" /><circle cx="12" cy="19" r="2.2" /><path d="m7 7 9.8-.8M6.3 8.8l4.5 8.4M17.8 8l-4.6 9" />
    </g>
    <g v-else-if="name === 'document' || name === 'source'">
      <path d="M6 3h8l4 4v14H6z" /><path d="M14 3v5h4M9 12h6M9 16h6" />
    </g>
    <g v-else-if="name === 'research'">
      <path d="M8 4h8M9 2h6v4H9zM6 4h12a2 2 0 0 1 2 2v15H4V6a2 2 0 0 1 2-2Z" /><path d="m8 14 2.2 2.2L16 10.5" />
    </g>
    <g v-else-if="name === 'search'">
      <circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" />
    </g>
    <g v-else-if="name === 'person'">
      <circle cx="12" cy="7.5" r="3.5" /><path d="M5 21c.5-4.4 3-6.8 7-6.8s6.5 2.4 7 6.8" />
    </g>
    <g v-else-if="name === 'line'">
      <path d="M6 4v5a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v5M6 20v-5a3 3 0 0 1 3-3" /><circle cx="6" cy="4" r="2" /><circle cx="6" cy="20" r="2" /><circle cx="18" cy="20" r="2" />
    </g>
    <g v-else-if="name === 'event'">
      <rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18M8 14h3M8 17h7" />
    </g>
    <g v-else-if="name === 'household'">
      <path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7" />
    </g>
    <g v-else-if="name === 'case'">
      <path d="M3 7h7l2-3h9v16H3z" /><path d="M3 10h18" />
    </g>
    <g v-else-if="name === 'question'">
      <circle cx="12" cy="12" r="9" /><path d="M9.7 9a2.5 2.5 0 0 1 4.8 1c0 2-2.5 2.2-2.5 4M12 18h.01" />
    </g>
    <g v-else-if="name === 'volume'">
      <path d="M4 4h7a3 3 0 0 1 3 3v14a3 3 0 0 0-3-3H4zM20 4h-6v17a3 3 0 0 1 3-3h3z" />
    </g>
    <g v-else-if="name === 'run'">
      <circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2.1 2.1M16.9 16.9 19 19M19 5l-2.1 2.1M7.1 16.9 5 19" />
    </g>
    <g v-else-if="name === 'copy'">
      <rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
    </g>
    <g v-else-if="name === 'lock'">
      <rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </g>
    <path v-else d="M5 12h14M13 6l6 6-6 6" />
  </svg>
</template>
