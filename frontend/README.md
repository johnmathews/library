# frontend

The Library web UI — Vue 3.5 + Vite 8 + Pinia + vue-router, styled with
the GOV.UK Design System (govuk-frontend 6.2) using self-hosted Inter and
a text-only masthead. See [docs/frontend.md](../docs/frontend.md) for the
design-system approach, component inventory, and auth integration.

## Commands

```sh
npm install            # install dependencies
npm run dev            # dev server (proxies /api to localhost:8000)
npm run test:unit      # Vitest (add -- --run for a single pass)
npm run lint           # eslint
npm run type-check     # vue-tsc
npm run build          # type-check + production build into dist/
npm run check:assets   # licensing gate: no GDS Transport / crown assets
                       # in dist/ (run after build; CI does this)
```

## Licensing note

govuk-frontend's code is MIT, but the GDS Transport typeface and crown
imagery are licence-restricted to gov.uk services and must never be
bundled, served, or referenced. `npm run check:assets` enforces this
against the production build.
