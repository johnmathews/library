"""Admin-only views: system/infra context, architecture docs, test coverage,
user management, reference-entity (recipient/sender/kind) CRUD, and currency/FX.

The whole router is gated by ``require_admin`` (attached at include level in
app.py), so every endpoint here is admin-only; ``current_user`` still runs
first, so anonymous requests get 401 and merely non-admin requests get 403.
See docs/admin.md.

Structure: the single ``router`` and the cross-cutting helpers live in
``_base``; the domain submodules hang their ``@router.<verb>`` routes on it.
They are imported here purely for that decorator side effect, in the order that
preserves the original OpenAPI operation order (users → taxonomy → fx). Only
``router`` is re-exported — app.py mounts ``library.api.admin.router``.
"""

from library.api.admin._base import router

# Imported purely for the decorator side effect: each submodule hangs its routes
# on the shared ``router``. Import order == OpenAPI operation order, so it is
# pinned to users → taxonomy → fx (isort left off so it is not alphabetised).
# isort: off
from library.api.admin import users  # noqa: F401
from library.api.admin import taxonomy  # noqa: F401
from library.api.admin import fx  # noqa: F401

# isort: on

__all__ = ["router"]
