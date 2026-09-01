/**
 * A chart rule, in English.
 *
 * Shared rather than local because there are now two places that render the
 * same rule as prose — the draft flow's summary line and the rule editor's
 * preview — and two copies would drift into visibly disagreeing about the same
 * rule, which is a worse failure than either wording alone.
 *
 * `facets` is optional and defaults to none. Without it the raw facet and value
 * *keys* are printed, which is what the draft flow does: it holds no
 * vocabulary, and adding a fetch there to gain labels would be a larger change
 * than the improvement is worth. With it, keys resolve to the labels a reader
 * sees everywhere else in the UI. A key the vocabulary does not contain falls
 * back to the key itself rather than disappearing — the same rule the editor's
 * value chips follow, defined once here: a rule naming a deleted value must
 * stay visible and repairable, never silently shrink.
 */

import type { FacetRef } from '@/api/facets'
import type { Rule } from '@/api/spending'

function facetLabel(key: string, facets: FacetRef[]): string {
  return facets.find((facet) => facet.key === key)?.label ?? key
}

function valueLabel(facetKey: string, valueKey: string, facets: FacetRef[]): string {
  const facet = facets.find((candidate) => candidate.key === facetKey)
  return facet?.values.find((value) => value.key === valueKey)?.label ?? valueKey
}

/** One line describing what a rule matches. */
export function ruleSummary(rule: Rule, facets: FacetRef[] = []): string {
  if (rule.all.length === 0) return 'Every document.'
  return rule.all
    .map((clause) => {
      const values = clause.values.map((value) => valueLabel(clause.facet, value, facets))
      return `${facetLabel(clause.facet, facets)} ${clause.op === 'in' ? 'is' : 'is not'} ${values.join(' or ')}`
    })
    .join(' and ')
}

/** One line describing the split axis, if there is one. */
export function splitSummary(split: string | null, facets: FacetRef[] = []): string {
  return split ? `Split by ${facetLabel(split, facets)}.` : 'Not split.'
}
