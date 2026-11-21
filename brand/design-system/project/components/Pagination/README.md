Pagination: how many items are showing, then previous, the page numbers and next.

## Use

- The count on the left in `type-caption` and tabular figures: "Showing 25–48 of 312".
- Outline buttons for "Previous" and "Next" with a chevron. Page numbers are ghost buttons, 34px square; a gap shows as "…".
- **The current page:** `brand-tint` with `brand-on-tint` at 650 and `aria-current="page"`.
- Each page button is named "Page 3"; "Previous" and "Next" say "page" to a screen reader.
- On a phone the row wraps under the count.

## States

On the first page "Previous" is unavailable (`aria-disabled`, 40%), and on the last page "Next" is.

## You provide

The current page, the page count, the total and the handler. Today: `components/app/AppPagination.vue`.
