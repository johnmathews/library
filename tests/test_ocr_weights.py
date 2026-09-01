"""The vendored RapidOCR weights are present, correct, and what the pins ask for.

These are fast (no engine construction, no network) and are the guard that
replaced a silent download. Two distinct failures they must separate:

- A committed file is missing or corrupt -> repopulate and commit.
- A rapidocr bump repointed a pinned stage at a *different* model -> the
  committed file is now the wrong one, which used to mean the engine quietly
  downloaded the new one on the deployed host and nobody knew. Here it is a
  red test naming the model that moved.

See ``library.ocr.weights`` and GH #109.
"""

import hashlib
from pathlib import Path

import pytest

from library.ocr import weights
from scripts.fetch_ocr_models import check, file_sha256, problem


@pytest.fixture(scope="module")
def pinned() -> tuple[weights.PinnedModel, ...]:
    return weights.pinned_models()


def test_every_pinned_stage_resolves(pinned: tuple[weights.PinnedModel, ...]) -> None:
    """All three stages resolve, to three distinct files under MODEL_DIR."""
    assert {model.task.value for model in pinned} == {"det", "cls", "rec"}
    assert len({model.path for model in pinned}) == 3
    for model in pinned:
        assert model.path.parent == weights.MODEL_DIR


def test_vendored_files_match_the_pinned_checksums(
    pinned: tuple[weights.PinnedModel, ...],
) -> None:
    """The committed bytes are the bytes rapidocr's model list asks for.

    This is the assertion that goes red on a rapidocr bump that repoints a
    stage: the pin resolves to a new URL with a new SHA256, and the file we
    ship no longer matches it.
    """
    for model in pinned:
        assert model.path.is_file(), (
            f"{model.task.value} weights missing at {model.path} — "
            "run `python -m scripts.fetch_ocr_models`"
        )
        assert file_sha256(model.path) == model.sha256, (
            f"{model.task.value} weights at {model.path} are not the pinned model — "
            "run `python -m scripts.fetch_ocr_models` and commit the result"
        )


def test_the_directory_holds_the_pinned_weights_and_nothing_else() -> None:
    """No superseded `.onnx` left behind after a pin change.

    History-size housekeeping, and the only part of it that is actually
    controllable. These files barely compress (gzip -9 gets ~0.92), so every
    distinct blob costs close to its full ~13 MB in the pack, forever. That
    price is unavoidable when a pin genuinely moves to different weights — but
    paying it *twice*, because the replaced file was added and the old one
    never deleted, is not.

    Measured churn is low: our three models were introduced in rapidocr 3.8.0
    and their checksums are unchanged across every release through 3.9.2, so
    the natural rate is roughly one revision per year or two. Scoped to
    `*.onnx` deliberately — a stray `.DS_Store` is not a history problem, and
    a stray 13 MB model is.
    """
    expected = {model.path.name for model in weights.pinned_models()}
    actual = {path.name for path in weights.MODEL_DIR.glob("*.onnx")}
    assert actual == expected, (
        f"unexpected weights in {weights.MODEL_DIR}: "
        f"extra={sorted(actual - expected)}, missing={sorted(expected - actual)}. "
        "Delete superseded models rather than leaving them alongside the new ones — "
        "each one is ~13 MB in the pack permanently."
    )


def test_nothing_missing_in_a_healthy_checkout() -> None:
    assert weights.missing_models() == ()


def test_engine_params_point_at_the_vendored_directory() -> None:
    """The engine is built against MODEL_DIR — the whole offline guarantee."""
    params = weights.engine_params()
    assert params["Global.model_root_dir"] == str(weights.MODEL_DIR)


def test_engine_params_pin_all_four_axes_per_stage() -> None:
    """No axis is left to rapidocr's defaults.

    The regression this protects against is concrete: rapidocr 3.9.x moved the
    Det default from PP-OCRv4/mobile to PP-OCRv6/small, and an implicit
    ``Det.model_type`` alongside an explicit ``Det.ocr_version`` produced a
    combination with no model, raising on every photo.
    """
    params = weights.engine_params()
    for stage in ("Det", "Cls", "Rec"):
        for axis in ("engine_type", "ocr_version", "lang_type", "model_type"):
            assert f"{stage}.{axis}" in params, f"{stage}.{axis} left implicit"


def test_missing_models_reports_the_absent_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Point MODEL_DIR at an empty directory and every stage is reported.

    Exercises the branch ``/healthz`` takes on a deploy whose image lost the
    COPY — the case that otherwise only shows up as a failed ingest.
    """
    monkeypatch.setattr(weights, "MODEL_DIR", tmp_path)
    weights.pinned_models.cache_clear()
    try:
        missing = weights.missing_models()
        assert {model.task.value for model in missing} == {"det", "cls", "rec"}
    finally:
        weights.pinned_models.cache_clear()


def test_check_fails_on_a_corrupt_weight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file of the right name but the wrong bytes is a mismatch, not a pass.

    A truncated layer or a half-written download leaves exactly this shape, and
    it is the one an existence check cannot see — which is why compose-smoke
    runs ``--check`` rather than ``test -f``.
    """
    monkeypatch.setattr(weights, "MODEL_DIR", tmp_path)
    weights.pinned_models.cache_clear()
    try:
        for model in weights.pinned_models():
            model.path.write_bytes(b"not an onnx graph")
        assert check() == 1
        stderr = capsys.readouterr().err
        assert "checksum mismatch" in stderr
        assert "fetch_ocr_models" in stderr
    finally:
        weights.pinned_models.cache_clear()


def test_problem_distinguishes_missing_from_mismatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two failure modes need different fixes, so they get different words."""
    monkeypatch.setattr(weights, "MODEL_DIR", tmp_path)
    weights.pinned_models.cache_clear()
    try:
        model = weights.pinned_models()[0]
        detail = problem(model)
        assert detail is not None and detail.startswith("missing:")

        body = b"wrong"
        model.path.write_bytes(body)
        detail = problem(model)
        assert detail is not None and detail.startswith("checksum mismatch:")
        assert hashlib.sha256(body).hexdigest() in detail
    finally:
        weights.pinned_models.cache_clear()
