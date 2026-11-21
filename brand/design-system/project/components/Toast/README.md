A toast: a short note in the top right corner that something in the background finished or failed.

## Use

- `panel`, `radius-md`, `shadow-pop`, 320px wide (the screen less 32px on a phone), stacked 8px apart, newest on top.
- An icon, a title at 600 and one line in `type-caption`: "Document processed · Annual energy statement 2026".
- **Done:** a check icon in `ink`. **Failed:** an alert icon and the title in `error-ink`, with a link to what helps ("See the job"). **Information:** an info icon in `ink`.
- No coloured edge: the icon and the words carry the kind.
- A close button named "Dismiss". A toast that needs a decision is a Banner or a Dialog instead.

## States

Errors carry `role="alert"` and are announced at once; the rest are `role="status"`. Each enters with a short fade, off under `prefers-reduced-motion`.

## You provide

The kind, the title and the line. Today: `components/app/ToastContainer.vue` and `stores/notifications.ts`.
