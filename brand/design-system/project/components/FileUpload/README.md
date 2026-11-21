The file upload: a drop zone for documents, then each file's progress and result in a list under it.

## Use

- **Drop zone:** a 1.5px dashed `control-border` on `panel`, `radius-md`, an upload icon in `ink-secondary`, "Drop files here, or choose files" with "choose files" as a link that opens the picker. Under it, in `type-caption`, what it takes: "PDFs, photos and text notes, up to 100 MB each."
- On a phone the picker offers the camera and the photo library; the zone says nothing about dragging.
- **The list:** a card, one row per file, the file name in `type-code`, and its state as a chip on the right.
- **Progress:** a 6px `brand-solid` bar on a `tile` track while a file uploads or processes, with `role="progressbar"`.
- The primary "Upload" sends what is chosen. A screen-wide count says how far it is ("Uploading… 2 of 5").

## States

- Dragging over: `brand` edge on `brand-tint`.
- Processing: a neutral chip "Processing" over the progress bar.
- Indexed: a neutral chip with a check icon. Green is for money, so a finished file is not green.
- Already in the library: a neutral chip and a link to the existing document.
- Failed: the Failed chip (`error-ink` on `deficit-tint`, alert icon) and a line that says why and what to do: "The file is password protected. See the job".

## You provide

The files, each one's state and progress, and the reason for a failure. Today: `components/app/AppFileUpload.vue` in `views/UploadView.vue`.
