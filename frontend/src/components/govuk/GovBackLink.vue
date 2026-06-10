<script setup lang="ts">
const props = withDefaults(defineProps<{ text?: string; to?: string; href?: string }>(), {
  text: 'Back',
})
</script>

<template>
  <RouterLink v-if="props.to" :to="props.to" class="govuk-back-link">{{ props.text }}</RouterLink>
  <a v-else :href="props.href ?? '#'" class="govuk-back-link">{{ props.text }}</a>
</template>
