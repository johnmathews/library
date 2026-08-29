/**
 * Typed API for payment identity (docs/money-facts.md).
 *
 * Roughly a quarter of this archive's amount-bearing documents are one real
 * payment documented twice (an emailed invoice and a downloaded receipt, a
 * booking confirmation and its payment confirmation). The backend collapses
 * those into a single payment; this client surfaces that group and offers a
 * split when the collapse was wrong.
 */

import { apiFetch } from './client'

export interface PaymentDocumentRef {
  id: number
  title: string | null
  document_date: string | null
  amount_kind: string | null
  reference: string | null
}

export interface PaymentRef {
  payment_id: number
  documents: PaymentDocumentRef[]
}

/** GET /api/documents/{id}/payment — the payment group this document belongs to. */
export function fetchPayment(id: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>(`/api/documents/${id}/payment`)
}

/**
 * POST /api/payments/merge — join two documents' payments into one.
 * 404 if either id is unknown or soft-deleted; 422 if `docA === docB`.
 */
export function mergePayment(docA: number, docB: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>('/api/payments/merge', {
    method: 'POST',
    body: { doc_a: docA, doc_b: docB },
  })
}

/**
 * POST /api/payments/split — pull two documents apart into separate payments.
 * 404 if either id is unknown or soft-deleted; 422 if `docA === docB`.
 */
export function splitPayment(docA: number, docB: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>('/api/payments/split', {
    method: 'POST',
    body: { doc_a: docA, doc_b: docB },
  })
}
