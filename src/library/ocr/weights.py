"""The pinned RapidOCR model weights: where they live and what they must be.

The photo OCR path runs three ONNX models (detection, classification,
recognition). They are **committed to this repository** under ``models/ocr/``
and shipped in the image, rather than downloaded on first use.

Why they are vendored
---------------------
rapidocr resolves a model URL from its bundled ``default_models.yaml`` and
fetches it into ``Global.model_root_dir`` the first time an engine is built.
Two of our three pinned models are not the ones bundled in the wheel, so two
downloads from ``modelscope.cn`` were armed on every fresh container — and
nothing persisted them, so ``docker compose up --force-recreate`` re-armed
them. The failure was late (``get_engine`` is lazy and ``lru_cache``d, so a
deploy looked healthy and ``/healthz`` passed) and per-document: the first
photo, PNG, HEIC or low-confidence scan ingested after a deploy failed its OCR,
and nothing before that moment could tell. ``modelscope.cn`` being unreachable
on 2026-08-28 is what surfaced it. See GH #109.

Vendoring removes the third-party host from ingest, from the image build and
from CI in one move: rapidocr skips a download entirely when the file already
exists at the expected path with the expected SHA256, which is exactly what
``models/ocr/`` provides.

The price: ~13 MB in the repository (they compress badly — gzip -9 gets only
~0.92, and the measured pack went 2.69 -> 15.91 MiB), and another near-full
copy in history each time a pin moves to genuinely different weights. Measured churn is low. These
three models were introduced in rapidocr 3.8.0 and their checksums are
identical in every release through 3.9.2; the upstream URL carries the rapidocr
version tag and so changes every release, but ``pinned_models()`` compares the
*file's* SHA256 rather than its URL, so a version bump alone costs nothing.

Single source of truth
----------------------
The filenames and checksums are not restated here — ``pinned_models()`` asks
rapidocr's own model list what each pinned stage resolves to. A rapidocr
release that repoints a model therefore changes what this module expects, and
``tests/test_ocr_weights.py`` goes red against the committed files instead of
the old behaviour (a silent re-download of something nobody chose). Recover
with ``python -m scripts.fetch_ocr_models``.

Neither ``cv2`` nor ``onnxruntime`` is imported here, deliberately, so
``/healthz`` and the shipped-in-the-image check can use this module without
paying for the inference stack. ``rapidocr.utils.typings`` is pure ``enum`` and
``rapidocr``'s package init is lazy, so the module-level import below is free;
``rapidocr.inference_engine.base`` parses a 72 KB YAML at import, so it stays
inside the functions that need it.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rapidocr.utils.typings import (
    EngineType,
    LangCls,
    LangDet,
    LangRec,
    ModelType,
    OCRVersion,
    TaskType,
)

if TYPE_CHECKING:
    from rapidocr.inference_engine.base import FileInfo

# ``src/library/ocr/weights.py`` -> repository root. The project is installed
# editable (``uv sync`` in the Dockerfile's builder stage), so inside the image
# this resolves to ``/app`` and finds the ``COPY models/`` from the runtime
# stage — the same trick ``library.cli.RECALL_BASELINE_PATH`` uses.
MODEL_DIR: Path = Path(__file__).resolve().parents[3] / "models" / "ocr"

# Every axis that selects a model file is pinned explicitly, including the ones
# whose value equals today's rapidocr default. rapidocr resolves a model from
# the (engine_type, ocr_version, lang_type, model_type) tuple per stage and
# raises ``ValueError("Invalid OCR configuration.")`` when the combination has
# no entry in its model list. Inheriting any axis from the library's defaults
# means a rapidocr release can repoint or break our pipeline without us
# changing a line: 3.9.x moved the Det/Rec defaults from ``mobile``/PP-OCRv4 to
# ``small``/PP-OCRv6, and because ``Det.model_type`` was left implicit while
# ``Det.ocr_version`` was pinned to PP-OCRv5, the resulting PP-OCRv5-det-
# ``small`` pair does not exist and every photo OCR call raised. Pin all four
# per stage; a bump then fails loudly at the pin, which is a diff we can read,
# rather than silently selecting a model we never chose.
#
# Det/Cls are language-agnostic layout stages — the "ch" models are the only
# detection weights shipped for PP-OCRv5 and are what the latin recognition
# model is meant to be paired with.
_PINS: dict[TaskType, tuple[OCRVersion, LangDet | LangCls | LangRec, ModelType]] = {
    TaskType.DET: (OCRVersion.PPOCRV5, LangDet.CH, ModelType.MOBILE),
    TaskType.CLS: (OCRVersion.PPOCRV4, LangCls.CH, ModelType.MOBILE),
    TaskType.REC: (OCRVersion.PPOCRV5, LangRec.LATIN, ModelType.MOBILE),
}


@dataclass(frozen=True)
class PinnedModel:
    """One pinned stage: where its weights must be, and what they must hash to."""

    task: TaskType
    path: Path
    sha256: str
    url: str


def engine_params() -> dict[str, Any]:
    """The ``params`` dict for ``RapidOCR(...)``: the pins, pointed at ``MODEL_DIR``.

    ``Global.model_root_dir`` is what keeps ingest offline. rapidocr looks for
    ``<model_root_dir>/<basename of the pinned URL>`` and only reaches for the
    network when that file is absent or its SHA256 does not match.

    Derived from ``_PINS`` rather than written out again, so the params the
    engine is built with and the files ``pinned_models()`` demands cannot drift
    apart.
    """
    params: dict[str, Any] = {"Global.model_root_dir": str(MODEL_DIR)}
    for task, (ocr_version, lang_type, model_type) in _PINS.items():
        stage = task.value.capitalize()  # det -> Det, matching rapidocr's config keys
        params[f"{stage}.engine_type"] = EngineType.ONNXRUNTIME
        params[f"{stage}.ocr_version"] = ocr_version
        params[f"{stage}.lang_type"] = lang_type
        params[f"{stage}.model_type"] = model_type
    return params


def _file_info(task: TaskType) -> "FileInfo":
    """rapidocr's model-list lookup key for one pinned stage."""
    from rapidocr.inference_engine.base import FileInfo

    ocr_version, lang_type, model_type = _PINS[task]
    return FileInfo(
        engine_type=EngineType.ONNXRUNTIME,
        ocr_version=ocr_version,
        task_type=task,
        lang_type=lang_type,
        model_type=model_type,
    )


@lru_cache(maxsize=1)
def pinned_models() -> tuple[PinnedModel, ...]:
    """What the pins resolve to today, straight out of rapidocr's model list.

    Cached because it parses rapidocr's 72 KB ``default_models.yaml``, and the
    answer cannot change within a process. Propagates whatever rapidocr raises
    when a pin no longer resolves — a ``ValueError`` there is the loud failure
    the pins exist to produce.
    """
    from rapidocr.inference_engine.base import InferSession

    models: list[PinnedModel] = []
    for task in _PINS:
        info = InferSession.get_model_url(_file_info(task))
        url = str(info["model_dir"])
        models.append(
            PinnedModel(
                task=task,
                path=MODEL_DIR / Path(url).name,
                sha256=str(info["SHA256"]),
                url=url,
            )
        )
    return tuple(models)


def missing_models() -> tuple[PinnedModel, ...]:
    """The pinned models whose file is absent from ``MODEL_DIR``.

    Existence only — no hashing. This runs per ``/healthz`` request and the
    files total ~13 MB, so checksum verification belongs in the test suite and
    in ``scripts/fetch_ocr_models.py --check``, not on a request path.
    """
    return tuple(model for model in pinned_models() if not model.path.is_file())
