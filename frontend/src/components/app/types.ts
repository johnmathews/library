/** A single error in the error summary. `href` should point at the id of the field. */
export interface ErrorSummaryItem {
  text: string
  href?: string
}

/** Option for AppSelect. */
export interface SelectItem {
  value: string
  text: string
  disabled?: boolean
}

/**
 * Item for AppRadios / AppCheckboxes. When `conditional` is true the slot
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

/** Row for AppSummaryList. */
export interface SummaryListRow {
  key: string
  value: string
  actions?: SummaryListAction[]
}
