/** A single error in the error summary. `href` should point at the id of the field. */
export interface ErrorSummaryItem {
  text: string
  href?: string
}

/** Option for GovSelect. */
export interface SelectItem {
  value: string
  text: string
  disabled?: boolean
}

/**
 * Item for GovRadios / GovCheckboxes. When `conditional` is true the slot
 * named `conditional-<value>` is revealed while the item is selected.
 */
export interface ChoiceItem {
  value: string
  text: string
  hint?: string
  conditional?: boolean
}

/** Action link on a summary list row. */
export interface SummaryListAction {
  text: string
  href: string
  visuallyHiddenText?: string
}

/** Row for GovSummaryList. */
export interface SummaryListRow {
  key: string
  value: string
  actions?: SummaryListAction[]
}

/**
 * Navigation item for GovServiceNavigation. `to` renders a RouterLink;
 * `button: true` renders a real `<button>` styled like the nav links
 * (app extension `app-nav-button` — for items that open a dialog rather
 * than navigate, e.g. Search). Buttons and plain `<a>` action items
 * both emit `select`.
 */
export interface ServiceNavigationItem {
  text: string
  to?: string
  href?: string
  active?: boolean
  /** Render a `<button type="button">` instead of a link. */
  button?: boolean
  /** `aria-haspopup` for button items that open a popup (e.g. 'dialog'). */
  ariaHasPopup?: 'dialog' | 'menu' | 'listbox' | 'tree' | 'grid' | 'true'
}
