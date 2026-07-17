"""Unlock password-protected PDFs at ingest.

Pure over bytes (pikepdf only — no DB, no config), so it is unit-testable
without a fixture. The single entry point ``unlock_pdf`` is called from
``library.ingest.ingest_file`` for every ``application/pdf`` upload: a success
replaces the stored bytes with the decrypted PDF so the whole app (dedup, OCR,
thumbnails, viewer, download) sees a normal unlocked file. This is safe because
the library app is itself behind authentication.
"""

import io
import logging
from collections.abc import Sequence

import pikepdf

logger = logging.getLogger(__name__)


class PdfLockedError(Exception):
    """An encrypted PDF that none of the supplied passwords could open.

    Reports only the number of passwords attempted — never their values — so
    the message is safe to log and to surface in an ingestion event.
    """

    def __init__(self, attempted: int) -> None:
        self.attempted = attempted
        super().__init__(
            f"encrypted PDF — none of the {attempted} configured password(s) unlocked it"
        )


def unlock_pdf(content: bytes, passwords: Sequence[str]) -> bytes:
    """Return decrypted PDF bytes, or ``content`` unchanged when no unlock is needed.

    - Opens without a password (not user-encrypted, incl. owner-only encryption)
      → return ``content`` unchanged.
    - User-encrypted → try the empty password, then each configured password in
      order; on the first that opens, save a decrypted copy and return its bytes.
    - None work → raise :class:`PdfLockedError`.
    - Not a PDF / corrupt PDF (pikepdf raises anything other than a
      ``PasswordError``) → return ``content`` unchanged. Best-effort by design:
      never fail the upload here — the OCR pipeline already handles unreadable
      PDFs (mirrors the docx conversion branch in ``ingest_file``).
    """
    try:
        with pikepdf.open(io.BytesIO(content)):
            return content  # opens without a password: nothing to unlock
    except pikepdf.PasswordError:
        pass  # user-encrypted — fall through to the password attempts
    except Exception:
        # Corrupt / not-a-PDF: leave it to the pipeline, don't fail ingest.
        logger.warning("pdf_unlock: could not open PDF to check encryption", exc_info=True)
        return content

    # The empty password is always tried first (owner-only PDFs, and PDFs whose
    # user password is the empty string), then the configured list in order.
    for password in ("", *passwords):
        try:
            with pikepdf.open(io.BytesIO(content), password=password) as pdf:
                out = io.BytesIO()
                pdf.save(out)
                return out.getvalue()
        except pikepdf.PasswordError:
            continue
        except Exception:
            # Encrypted but unreadable once opened/re-saved (a corrupt-and-
            # encrypted PDF, e.g. a mangled email attachment). Best-effort, same
            # guarantee as the initial open above: never fail the upload here —
            # return the original and let the pipeline surface it (the OCR router
            # re-checks encryption and fails the document with a clear reason).
            logger.warning("pdf_unlock: encrypted PDF unreadable during unlock", exc_info=True)
            return content

    raise PdfLockedError(len(passwords))
