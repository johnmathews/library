Choices: a checkbox for an independent yes or no, a radio for one of a few, and a switch for a setting that applies at once.

## Use

- 16px boxes and circles with a 1.5px `control-border` on `panel`. Checked: `brand-fill` with an `on-brand` tick or dot.
- The label is the hit target too, in `type-body`, sentence case. A group has a `legend` in `type-label` ("Notify me about", "Document previews").
- The switch is 36 by 20, `tile` off and `brand-fill` on, with its state in words beside it ("On", "Off") so it never relies on colour.
- More than about seven options: a FilterPopover with search, not a column of boxes.

## States

Focus takes the 2px `focus` ring on the box. Unavailable fades to 40% with `aria-disabled`.

## You provide

The label, the value, and for radios the group's name. Today: `components/app/AppCheckboxes.vue`, `AppRadios.vue` and the `.form-checkbox` class.
