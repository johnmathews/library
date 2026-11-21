The button: a violet primary for the one action that matters on a screen, an outline button for the rest, a ghost for a quiet action inside a panel, and a square or round icon button.

## Use

- **Primary:** `brand-fill` with `on-brand` text, `radius-sm`, 34px tall, `type-label` at 13.5px. One per screen or dialog: "Upload", "Add comment", "Show 12", "Delete permanently".
- **Outline:** `panel` fill, 1px `control-border`, `ink` text. Everything that is not the main action: "Edit details", "Download", "Restore".
- **Ghost:** no fill or edge, `brand` text. Only inside a popover or dialog footer, beside a primary ("Clear").
- **Icon:** 34px square, or round in the header. Always has an `aria-label` ("Previous document", "Copy answer", "Dark mode").
- **No red button.** Red means something failed. A destructive action is the primary of its confirming Dialog, named by its verb ("Delete permanently").
- Labels are a verb and its object in sentence case. Icons sit left of the label at 16px.

## States

- Hover: outline and ghost take a `tile` fill; the primary brightens 10%.
- Focus: a 2px ring in `focus` (ink) at a 2px offset, on every variant. The ring sits on the surface around the button, so it takes the surface's contrast colour, not the label's: a white ring around a violet button vanishes on a white card.
- Working: full colour, `aria-busy="true"`, and the label says what is happening ("Uploading… 2 of 5", "Deleting…"). Never fade it.
- Unavailable: `aria-disabled="true"` at 40% opacity, never the `disabled` attribute, which drops focus.

## You provide

The label, the action, and the icon if any. In the app: `components/app/AppButton.vue`. `primary` is `brand-fill`, `secondary` is the outline, `warning` renders as the outline button (the name stays so call sites need not change; nothing is red), and `inverse` stays only on a violet confirmation panel. A confirming action (ConfirmDialog, the delete confirmation page) is the primary.
