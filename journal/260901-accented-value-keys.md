# Transliterating accented labels instead of dropping the accent

**Date:** 2026-09-01
**Branch:** `fix/113-accented-value-keys`
**Issue:** [#113](https://github.com/johnmathews/library/issues/113)

## 1. What went

`derive_value_key` — the one function that manufactures a facet value key, on
the one route that widens the closed vocabulary — sanitised a suggested label
by deleting every character outside `[a-z0-9_-]`. Applied to an accented
label, that deletes the *letter*, not the accent:

| suggested label | before | after |
| --- | --- | --- |
| `Škoda` | `koda` | `skoda` |
| `Citroën` | `citron` | `citroen` |

The fix is an NFKD normalisation with the combining marks stripped, applied
*before* the existing filter. Everything downstream of it is unchanged.

## 2. Why the key and not the label

The label was always fine — it is stored verbatim and always was. It is the
key that was wrong, and the asymmetry between them is the whole reason this
was worth fixing ahead of things with more visible symptoms:

- a value's **label** can be renamed at any time, for free;
- a value's **key** is the stable identifier that every rule and every
  `document_labels` row references, so changing one is a migration.

So the damage window is "until somebody accepts an accented suggestion", and
after that it is permanent. Nothing in the live archive carries a mangled key
today — the existing values were created by hand with sensible keys — but the
labelling run has queued suggestions for accented marques, and accepting any
one of them through the UI would have minted one.

## 3. The part that needed care: this is derivation, not matching

`docs/facets.md` §3 already records a settled decision that value/alias
*matching* is case-insensitive but deliberately **not** accent-insensitive:
`str.casefold()` folds case and not diacritics, so an accented canonical
spelling needs its unaccented form as an explicit alias. There is an executed
test pinning it, `test_casefold_does_not_fold_diacritics`.

It would have been easy to "tidy" that while here, and wrong — so the doc now
says explicitly that folding applies to derivation only.

The same test also caught the one real trap. Its fixture vocabulary keys a
value `koda`, and a comment above it stated — correctly, at the time — that
`koda` is exactly what `derive_value_key("Škoda")` returns. Two things had to
happen there and only one of them was obvious:

1. the comment is now false, so it was rewritten; but
2. the fixture key must **not** be updated to `skoda`, because the test proves
   that the model emitting an unaccented `Skoda` matches *neither* the alias
   nor the key and falls through to a suggestion. A key of `skoda` would match
   directly and would silently invert what the test checks.

The comment now says why it is deliberately left as a legacy-shaped key. That
is also true of real archives: existing keys are not rewritten by this change.

## 4. What is deliberately still refused

A label with no Latin form — entirely Greek, or Japanese — does not decompose
into ASCII, so it still folds to nothing and still returns `""`, which
`accept_suggestion` answers with a `422`. Transliteration must not rescue such
a label into some arbitrary key; a refusal the owner can act on is better than
a key nobody can read. Covered by
`test_derive_value_key_still_returns_empty_when_nothing_transliterates` and
`test_accepting_an_entirely_non_latin_suggestion_is_422`.

German `ß` is the known edge that NFKD does not decompose (a full
transliteration would give `ss`). It is dropped, as before. Left alone
deliberately: no value needs it, and guessing at language-specific expansions
is a larger decision than this fix.

## 5. Verification

Six parametrized derivation cases and the end-to-end accept were each run RED
against the unmodified function first (`koda` vs `skoda`, `citron` vs
`citroen`), then green. `tests/test_facet_labeller.py` ran green unmodified,
which is what proves matching is untouched. Full backend suite green: 2090
passed, 7 skipped. `ruff check`, `ruff format --check` and `mypy` clean.
