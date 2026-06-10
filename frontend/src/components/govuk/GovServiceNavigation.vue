<script setup lang="ts">
import { ref } from 'vue'
import { ServiceNavigation } from 'govuk-frontend'
import { useGovukComponent } from './useGovukComponent'
import type { ServiceNavigationItem } from './types'

const props = defineProps<{
  items: ServiceNavigationItem[]
  serviceName?: string
  serviceUrl?: string
}>()

const emit = defineEmits<{ select: [item: ServiceNavigationItem] }>()

const root = ref<HTMLElement | null>(null)

// govuk-frontend ServiceNavigation drives the mobile menu toggle button.
useGovukComponent(root, ServiceNavigation)

function onActionClick(event: Event, item: ServiceNavigationItem): void {
  if (!item.href) event.preventDefault()
  emit('select', item)
}
</script>

<template>
  <section
    ref="root"
    aria-label="Service information"
    class="govuk-service-navigation"
    data-module="govuk-service-navigation"
  >
    <div class="govuk-width-container">
      <div class="govuk-service-navigation__container">
        <span v-if="props.serviceName" class="govuk-service-navigation__service-name">
          <a :href="props.serviceUrl ?? '/'" class="govuk-service-navigation__link">
            {{ props.serviceName }}
          </a>
        </span>
        <nav aria-label="Menu" class="govuk-service-navigation__wrapper">
          <button
            type="button"
            class="govuk-service-navigation__toggle govuk-js-service-navigation-toggle"
            aria-controls="navigation"
            hidden
          >
            Menu
          </button>
          <ul class="govuk-service-navigation__list" id="navigation">
            <li
              v-for="item in props.items"
              :key="item.text"
              class="govuk-service-navigation__item"
              :class="{ 'govuk-service-navigation__item--active': item.active }"
            >
              <RouterLink
                v-if="item.to"
                class="govuk-service-navigation__link"
                :to="item.to"
                :aria-current="item.active ? 'true' : undefined"
              >
                <strong v-if="item.active" class="govuk-service-navigation__active-fallback">
                  {{ item.text }}
                </strong>
                <template v-else>{{ item.text }}</template>
              </RouterLink>
              <a
                v-else
                class="govuk-service-navigation__link"
                :href="item.href ?? '#'"
                :aria-current="item.active ? 'true' : undefined"
                @click="onActionClick($event, item)"
              >
                <strong v-if="item.active" class="govuk-service-navigation__active-fallback">
                  {{ item.text }}
                </strong>
                <template v-else>{{ item.text }}</template>
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </div>
  </section>
</template>
