/**
 * Entity descriptor for the shared taxonomy CRUD panel (senders / recipients /
 * kinds). Senders and recipients are id-keyed and support rename-with-merge;
 * kinds are slug-keyed with no merge (a name collision is a hard 409). The
 * descriptor captures every point where those contracts diverge so the shared
 * component can stay behaviour-identical to the three original inline blocks.
 */

/** Row key: numeric id for senders/recipients, slug string for kinds. */
export type TaxonomyKey = string | number

/** The fields every taxonomy row surfaces in the shared panel. */
export interface TaxonomyRow {
  name: string
  document_count: number
}

/** The merge target read off a 409 rename body (id-entities only). */
export interface TaxonomyMergeTarget {
  target_id: number
  target_name: string
  target_document_count: number
}

export interface TaxonomyDescriptor<T extends TaxonomyRow> {
  /** testid/id prefix + label noun, e.g. 'sender'. Plural lists use `${testid}s`. */
  testid: string
  /** Section heading, e.g. 'Senders'. */
  heading: string
  /** Create-control label, e.g. 'Add sender'. */
  addLabel: string
  /** Rename-input label, e.g. 'Sender name'. */
  renameLabel: string
  /** Reassign "None (clear …)" option text, e.g. 'None (clear sender)'. */
  clearText: string
  /** Lowercase noun used in "the {noun}" error/confirm strings. */
  noun: string
  /** Rename collisions offer a merge (id-entities) or hard-error (kinds). */
  hasMerge: boolean
  /** Row key accessor: id for senders/recipients, slug for kinds. */
  keyOf: (row: T) => TaxonomyKey
  list: () => Promise<T[]>
  create: (name: string) => Promise<unknown>
  rename: (key: TaxonomyKey, name: string, merge: boolean) => Promise<unknown>
  /** Delete; omit reassignTo to delete a zero-document row outright. */
  remove: (key: TaxonomyKey, reassignTo?: TaxonomyKey | null) => Promise<void>
  /** Parse a reassign select value ('' → null) into a target key. */
  parseReassign: (value: string) => TaxonomyKey | null
  /** Read the 409 merge body — id-entities only (kinds never merge). */
  readMergeBody?: (body: Record<string, unknown>) => TaxonomyMergeTarget
}
