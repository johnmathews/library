<script setup lang="ts">
// Library's logo (design system: AppIcon): the app icon's square beside the
// name. The square is the favicon's drawing (public/favicon.svg) inline, so it
// takes the system's colours and needs no request. The name hides when the
// desktop sidebar collapses; the square stays, so the collapsed sidebar still
// shows which app this is.
</script>

<template>
  <span class="flex items-center gap-2.5">
    <svg
      class="app-logo__square shrink-0"
      width="32"
      height="32"
      viewBox="0 0 32 32"
      aria-hidden="true"
      data-testid="app-logo-square"
    >
      <rect width="32" height="32" rx="7" class="app-logo__fill" />
      <g
        fill="none"
        class="app-logo__mark"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M7 5H13L19 11V23A2 2 0 0 1 17 25H7A2 2 0 0 1 5 23V7A2 2 0 0 1 7 5Z" />
        <path d="M13 5V11H19" />
        <path d="M9 15H14M9 19H11" />
        <circle cx="20" cy="22" r="7" class="app-logo__fill" stroke="none" />
        <circle cx="20" cy="22" r="5" class="app-logo__fill" />
        <path d="M23.5 25.5L26 28" stroke-width="3" />
      </g>
    </svg>
    <!-- A <span>, not a heading: the logo is persistent chrome, and PageHeader
         renders the page's real <h1>. -->
    <span
      class="app-logo__name text-lg font-[650] tracking-[-0.015em] text-ink lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200 whitespace-nowrap overflow-hidden"
      data-testid="app-logo-name"
      >Library</span
    >
  </span>
</template>

<style scoped>
.app-logo__fill {
  fill: var(--brand-fill);
}
.app-logo__mark {
  stroke: var(--on-brand);
}
</style>
