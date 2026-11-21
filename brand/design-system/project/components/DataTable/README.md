The data table: rows on a `panel` card for jobs, users and other records, with hairlines between them; on a phone, a list.

## Use

- On a Card. Headers in `type-small` at 600 and `ink-secondary`, sentence case: "Task", "Status", "Document", "Duration", "Received". Never the old uppercase header row.
- Rows separated by `line` hairlines, never zebra stripes. A hovered row takes `tile`.
- A status is a TagChip: neutral with a check for Succeeded, neutral for Running and Queued, red for Failed.
- A document is a link in `brand` at 500. Figures and times are tabular and right-aligned when they compare down the column ("4.2 s").
- **Below 640px:** a list. The task and its status on the first line, the document under it, then the time and duration in `type-caption`.
- Paging under the table: Pagination.

## You provide

The columns, the rows and the handlers for any row action. Today: the tables in `views/JobsView.vue` and `views/admin/AdminUsersPanel.vue`.
