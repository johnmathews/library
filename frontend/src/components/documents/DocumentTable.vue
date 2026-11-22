<script setup lang="ts">
/**
 * Dashboard "Table" layout (design system: DataTable): a Title column, then one
 * column per field the owner chose, in their order. Sentence-case headings in
 * 13px semibold `ink-secondary`, hairlines between rows, a hovered row on
 * `tile`, the amount right-aligned and tabular. Needs review sits beside the
 * title, since it is not one of the chosen fields.
 *
 * The title is the row's link (in `brand`, as the system's DataTable draws a
 * document). A click anywhere else on the row opens the document too, for the
 * mouse; the link stays the one keyboard stop. The view renders the list in
 * place of the table where the content area is narrower than TABLE_MIN_WIDTH.
 */
import { useRouter } from 'vue-router'
import { AppBadge } from '@/components/app'
import DocumentFieldValue from './DocumentFieldValue.vue'
import type { DocumentLayoutProps } from './layoutProps'
import type { DocumentListItem } from '@/api/documents'
import { FIELD_COLUMN_LABELS } from '@/utils/documentFields'

const props = defineProps<DocumentLayoutProps>()
const router = useRouter()

function openFromRow(event: MouseEvent, item: DocumentListItem): void {
  // The link handles its own clicks (including cmd/ctrl-click to a new tab);
  // a text selection is not a click to open.
  if ((event.target as HTMLElement).closest('a')) return
  if (window.getSelection()?.toString()) return
  void router.push({ name: 'document-detail', params: { id: item.id }, query: props.detailQuery })
}
</script>

<template>
  <div class="card overflow-x-auto" data-testid="doc-table">
    <table class="w-full text-sm">
      <thead class="text-[13px] text-ink-secondary border-b border-line">
        <tr>
          <th scope="col" class="text-left font-semibold px-4 py-3">Title</th>
          <th
            v-for="field in fields"
            :key="field"
            scope="col"
            class="font-semibold px-4 py-3 whitespace-nowrap"
            :class="field === 'amount' ? 'text-right' : 'text-left'"
            :data-testid="`doc-table-col-${field}`"
          >
            {{ FIELD_COLUMN_LABELS[field] }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <!-- The row click is a mouse shortcut to the title link, which is the
             keyboard and screen-reader path; the row itself is not a control. -->
        <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events -->
        <tr
          v-for="item in items"
          :id="`doc-card-${item.id}`"
          :key="item.id"
          class="cursor-pointer transition-colors hover:bg-tile"
          :data-kind="item.kind?.slug"
          data-testid="doc-card"
          @click="openFromRow($event, item)"
        >
          <td class="px-4 py-2.5 align-middle">
            <span class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <RouterLink
                class="font-medium text-brand hover:underline"
                :to="{ name: 'document-detail', params: { id: item.id }, query: detailQuery }"
              >
                {{ item.title ?? 'Untitled document' }}
              </RouterLink>
              <AppBadge
                v-if="item.review_status === 'needs_review'"
                colour="yellow"
                data-testid="review-badge"
                >Needs review</AppBadge
              >
            </span>
          </td>
          <td
            v-for="field in fields"
            :key="field"
            class="px-4 py-2.5 align-middle"
            :class="{
              'text-right whitespace-nowrap': field === 'amount',
              'whitespace-nowrap': field !== 'amount' && field !== 'tags' && field !== 'sender',
            }"
          >
            <DocumentFieldValue
              :item="item"
              :field="field"
              :compact-dates="true"
              variant="text"
              date-labels="none"
            />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
