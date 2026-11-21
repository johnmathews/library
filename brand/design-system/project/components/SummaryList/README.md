A summary list: label and value pairs for a document's details, with the label beside the value on desktop and above it on a phone.

## Use

- Inside a Card titled "Details", with "Edit details" as its link.
- Labels in `type-label` and `ink-secondary`, a 150px column, sentence case: "Kind", "Sender", "Recipient", "Date on document", "Amount", "Tags", "Matter", "File".
- Values in `type-body` and `ink`. A kind or a tag is a neutral TagChip, a file name is `type-code`, an amount is tabular.
- Rows separated by `line` hairlines, none above the first.
- A missing value says so in `ink-secondary` ("None yet"), never a dash alone.
- Below 640px each label sits over its value.

## You provide

The rows, each a label and a value, and any row's action link. Today: `components/app/AppSummaryList.vue` and the details panel of `components/DocumentMetadataEditor.vue`, whose uppercase labels become sentence case.
