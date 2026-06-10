"""Benchmark the OCR engines over a directory of real PDFs (W5).

For every PDF in the samples directory this script:

- runs text-layer extraction (pypdfium2) and records the route the router
  would choose (``text-layer`` vs ``ocr``, per ``text_layer_min_chars_per_page``);
- classifies each page as image-backed (a single image covering >= 50% of the
  page area: a scan or a scan-app export with an embedded OCR text layer)
  or vector/born-digital;
- on documents routed to OCR: runs the production Tesseract path (OCRmyPDF
  with the exact production flag set, then the TSV confidence probe) AND the
  photo path (300 dpi rasterize -> OpenCV preprocess -> RapidOCR), both
  wall-clock timed;
- with ``--force-scans``: also runs both OCR paths on scan-like documents
  (mostly image-backed pages) that the router would send to the text layer,
  by first stripping the embedded text layer (rasterize at 300 dpi to an
  image-only PDF) so ``--skip-text`` cannot skip the pages. This measures
  what the OCR pipeline would have produced had the scan arrived without an
  embedded text layer, and lets the gate thresholds be tuned on real scans;
- computes which engine the confidence gate would pick.

Privacy: stdout and the ``--json`` file contain filenames and text excerpts
and are for local eyes only. The ``--output`` markdown is ANONYMIZED
(documents are ``sample-NN`` in sorted filename order, no filenames, no
content) and is the only artifact meant for the repo.

Usage:
    uv run python scripts/ocr_benchmark.py <samples_dir> [--force-scans] \
        [--output docs/benchmarks/report.md] [--json results.json]
"""

import argparse
import dataclasses
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import img2pdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from library.config import Settings
from library.ocr import photo, tesseract
from library.ocr.base import OcrResult

EXCERPT_CHARS: int = 240
IMAGE_PAGE_AREA_FRACTION: float = 0.5
SCAN_LIKE_PAGE_FRACTION: float = 0.5
RASTER_DPI: int = 300


@dataclass(frozen=True, slots=True)
class EngineRun:
    """One timed engine invocation on one document."""

    engine: str
    seconds: float
    chars: int
    confidence: float | None
    pages: int | None
    excerpt: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SampleResult:
    """Everything measured for one sample document."""

    index: int
    filename: str
    pages: int
    image_backed_pages: int
    scan_like: bool
    text_layer_seconds: float
    text_layer_chars: int
    chars_per_page: float
    min_page_chars: int
    max_page_chars: int
    text_layer_excerpt: str
    route: str  # "text-layer" | "ocr"
    ocr_forced: bool
    tesseract_run: EngineRun | None
    photo_run: EngineRun | None
    gate_pick: str | None  # engine name the gate would keep
    gate_reason: str | None


def page_text_and_image_stats(pdf_path: Path) -> tuple[list[int], int, str]:
    """Per-page text-layer char counts, image-backed page count, full text."""
    counts: list[int] = []
    parts: list[str] = []
    image_backed = 0
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document[index]
            width, height = page.get_size()
            text_page = page.get_textpage()
            try:
                text = (text_page.get_text_bounded() or "").strip()
            finally:
                text_page.close()
            counts.append(len(text))
            parts.append(text)
            threshold = IMAGE_PAGE_AREA_FRACTION * width * height
            for obj in page.get_objects(max_depth=2):
                if obj.type != pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                left, bottom, right, top = obj.get_bounds()
                if (right - left) * (top - bottom) >= threshold:
                    image_backed += 1
                    break
    finally:
        document.close()
    return counts, image_backed, "\n\n".join(parts).strip()


def rasterize_to_image_pdf(pdf_path: Path, target: Path, dpi: int = RASTER_DPI) -> None:
    """Strip the text layer: render every page and rebuild an image-only PDF."""
    jpegs: list[bytes] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            image = document[index].render(scale=dpi / 72).to_pil()
            buffer = BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=90)
            jpegs.append(buffer.getvalue())
    finally:
        document.close()
    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    target.write_bytes(img2pdf.convert(jpegs, layout_fun=layout))


def excerpt(text: str) -> str:
    return " ".join(text.split())[:EXCERPT_CHARS]


def run_engine(name: str, func: Callable[..., OcrResult], *args: Any, **kwargs: Any) -> EngineRun:
    """Time one engine call, capturing failures instead of raising."""
    start = time.perf_counter()
    try:
        result: OcrResult = func(*args, **kwargs)
    except Exception as exc:  # benchmark must survive any single failure
        return EngineRun(
            engine=name,
            seconds=time.perf_counter() - start,
            chars=0,
            confidence=None,
            pages=None,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    return EngineRun(
        engine=name,
        seconds=time.perf_counter() - start,
        chars=len(result.text),
        confidence=result.confidence,
        pages=result.pages,
        excerpt=excerpt(result.text),
    )


def gate_decision(
    tesseract_run: EngineRun, photo_run: EngineRun, threshold: float
) -> tuple[str, str]:
    """Replicate router._tesseract_with_gate: which engine wins and why."""
    t_conf = tesseract_run.confidence
    if t_conf is not None and t_conf >= threshold:
        return "tesseract", f"tesseract conf {t_conf:.1f} >= threshold {threshold:.1f}"
    p_conf = photo_run.confidence if photo_run.confidence is not None else -1.0
    t_eff = t_conf if t_conf is not None else -1.0
    if p_conf > t_eff:
        return "rapidocr", f"retry won: rapidocr {p_conf:.1f} > tesseract {t_eff:.1f}"
    return "tesseract", f"retry lost: rapidocr {p_conf:.1f} <= tesseract {t_eff:.1f}"


def run_ocr_paths(
    pdf_path: Path, settings: Settings, *, strip_text_layer: bool
) -> tuple[EngineRun, EngineRun]:
    """Run the production Tesseract path and the photo path on one document."""
    with tempfile.TemporaryDirectory(prefix="ocr-bench-") as workdir:
        work = Path(workdir)
        source = pdf_path
        if strip_text_layer:
            source = work / "image-only.pdf"
            rasterize_to_image_pdf(pdf_path, source)
        tesseract_run = run_engine(
            "tesseract", tesseract.ocr_pdf, source, work, languages=settings.ocr_languages
        )
        photo_run = run_engine("rapidocr", photo.ocr_pdf_pages, source)
    return tesseract_run, photo_run


def bench_document(
    pdf_path: Path, index: int, settings: Settings, *, force_scans: bool
) -> SampleResult:
    """Measure one document through every applicable path."""
    start = time.perf_counter()
    page_chars, image_backed, full_text = page_text_and_image_stats(pdf_path)
    text_layer_seconds = time.perf_counter() - start
    pages = len(page_chars)
    total_chars = sum(page_chars)
    chars_per_page = total_chars / pages if pages else 0.0
    scan_like = pages > 0 and image_backed / pages >= SCAN_LIKE_PAGE_FRACTION

    routed_text_layer = pages > 0 and chars_per_page >= settings.text_layer_min_chars_per_page
    route = "text-layer" if routed_text_layer else "ocr"
    forced = routed_text_layer and scan_like and force_scans

    tesseract_run: EngineRun | None = None
    photo_run: EngineRun | None = None
    gate_pick: str | None = None
    gate_reason: str | None = None
    if route == "ocr" or forced:
        tesseract_run, photo_run = run_ocr_paths(pdf_path, settings, strip_text_layer=forced)
        gate_pick, gate_reason = gate_decision(
            tesseract_run, photo_run, settings.ocr_confidence_threshold
        )

    return SampleResult(
        index=index,
        filename=pdf_path.name,
        pages=pages,
        image_backed_pages=image_backed,
        scan_like=scan_like,
        text_layer_seconds=text_layer_seconds,
        text_layer_chars=total_chars,
        chars_per_page=chars_per_page,
        min_page_chars=min(page_chars, default=0),
        max_page_chars=max(page_chars, default=0),
        text_layer_excerpt=excerpt(full_text),
        route=route,
        ocr_forced=forced,
        tesseract_run=tesseract_run,
        photo_run=photo_run,
        gate_pick=gate_pick,
        gate_reason=gate_reason,
    )


def fmt(value: float | None, spec: str = ".1f") -> str:
    return "—" if value is None else format(value, spec)


def describe(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (
        f"n={len(values)} min={min(values):.1f} median={statistics.median(values):.1f} "
        f"mean={statistics.fmean(values):.1f} max={max(values):.1f}"
    )


def environment_info() -> dict[str, str]:
    tesseract_version = subprocess.run(
        ["tesseract", "--version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "tesseract": tesseract_version[0] if tesseract_version else "unknown",
    }


def render_markdown(results: list[SampleResult], settings: Settings) -> str:
    """The anonymized report body: per-sample table + aggregates."""
    env = environment_info()
    lines: list[str] = []
    lines.append("## Per-sample results (anonymized)")
    lines.append("")
    lines.append(
        "| sample | pages | image-backed pages | text-layer chars/page | route | OCR forced |"
        " tess conf | tess chars | tess s | rapidocr conf | rapidocr chars | rapidocr s |"
        " gate pick |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        t, p = r.tesseract_run, r.photo_run
        lines.append(
            f"| sample-{r.index:02d} | {r.pages} | {r.image_backed_pages} "
            f"| {r.chars_per_page:.0f} | {r.route} | {'yes' if r.ocr_forced else 'no'} "
            f"| {fmt(t.confidence) if t else '—'} | {t.chars if t else '—'} "
            f"| {fmt(t.seconds) if t else '—'} "
            f"| {fmt(p.confidence) if p else '—'} | {p.chars if p else '—'} "
            f"| {fmt(p.seconds) if p else '—'} | {r.gate_pick or '—'} |"
        )
    lines.append("")

    ocr_results = [r for r in results if r.tesseract_run is not None]
    text_results = [r for r in results if r.route == "text-layer"]
    tess_confs = [
        r.tesseract_run.confidence
        for r in ocr_results
        if r.tesseract_run and r.tesseract_run.confidence is not None
    ]
    photo_confs = [
        r.photo_run.confidence
        for r in ocr_results
        if r.photo_run and r.photo_run.confidence is not None
    ]
    tess_times = [r.tesseract_run.seconds for r in ocr_results if r.tesseract_run]
    photo_times = [r.photo_run.seconds for r in ocr_results if r.photo_run]
    text_times = [r.text_layer_seconds for r in results]

    lines.append("## Aggregates")
    lines.append("")
    lines.append(
        f"- Documents: {len(results)} ({len(text_results)} routed text-layer, "
        f"{len(results) - len(text_results)} routed OCR; OCR paths measured on "
        f"{len(ocr_results)} docs including forced scans)"
    )
    lines.append(
        f"- Scan-like docs (mostly image-backed pages with embedded text layer): "
        f"{sum(1 for r in results if r.scan_like)}"
    )
    lines.append(f"- Text-layer extraction seconds: {describe(text_times)}")
    lines.append(f"- Tesseract path seconds: {describe(tess_times)}")
    lines.append(f"- Photo path seconds: {describe(photo_times)}")
    lines.append(f"- Tesseract mean word confidence: {describe(tess_confs)}")
    lines.append(f"- RapidOCR mean box confidence: {describe(photo_confs)}")
    gate_picks = [r.gate_pick for r in ocr_results if r.gate_pick]
    lines.append(
        f"- Gate decisions: {gate_picks.count('tesseract')}x tesseract, "
        f"{gate_picks.count('rapidocr')}x rapidocr (threshold "
        f"{settings.ocr_confidence_threshold})"
    )
    retried = sum(
        1
        for r in ocr_results
        if r.tesseract_run
        and (
            r.tesseract_run.confidence is None
            or r.tesseract_run.confidence < settings.ocr_confidence_threshold
        )
    )
    lines.append(f"- Photo retries the gate would trigger: {retried}/{len(ocr_results)}")
    lines.append("")

    lines.append("## Threshold observations")
    lines.append("")
    text_cpp = [r.chars_per_page for r in text_results]
    ocr_cpp = [r.chars_per_page for r in results if r.route == "ocr"]
    lines.append(
        f"- chars/page among text-layer-routed docs: {describe(text_cpp)} "
        f"(threshold: {settings.text_layer_min_chars_per_page})"
    )
    lines.append(f"- chars/page among OCR-routed docs: {describe(ocr_cpp)}")
    lines.append(
        f"- Tesseract confidences vs gate threshold {settings.ocr_confidence_threshold}: "
        f"{sorted(round(c, 1) for c in tess_confs)}"
    )
    lines.append(f"- RapidOCR confidences: {sorted(round(c, 1) for c in photo_confs)}")
    lines.append("")

    lines.append("## Environment")
    lines.append("")
    for key, value in env.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)


def print_full_table(results: list[SampleResult]) -> None:
    """Stdout-only detail, including filenames and excerpts (never committed)."""
    for r in results:
        print(f"\nsample-{r.index:02d}: {r.filename}")
        print(
            f"  pages={r.pages} image_backed={r.image_backed_pages} scan_like={r.scan_like} "
            f"text_layer_chars={r.text_layer_chars} chars/page={r.chars_per_page:.0f} "
            f"(min={r.min_page_chars} max={r.max_page_chars}) route={r.route} "
            f"forced={r.ocr_forced} text_layer_s={r.text_layer_seconds:.2f}"
        )
        print(f"  text-layer excerpt: {r.text_layer_excerpt!r}")
        for run in (r.tesseract_run, r.photo_run):
            if run is None:
                continue
            print(
                f"  {run.engine}: conf={fmt(run.confidence)} chars={run.chars} "
                f"pages={run.pages} seconds={run.seconds:.1f} "
                + (f"ERROR={run.error}" if run.error else "")
            )
            print(f"    excerpt: {run.excerpt!r}")
        if r.gate_pick:
            print(f"  gate: {r.gate_pick} ({r.gate_reason})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples_dir", type=Path, help="directory of sample PDFs")
    parser.add_argument(
        "--force-scans",
        action="store_true",
        help="also run OCR paths on scan-like docs with embedded text layers (stripped first)",
    )
    parser.add_argument("--output", type=Path, default=None, help="anonymized markdown report")
    parser.add_argument("--json", type=Path, default=None, help="full-detail JSON (private)")
    args = parser.parse_args()

    settings = Settings()
    pdfs = sorted(args.samples_dir.glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs found in {args.samples_dir}", file=sys.stderr)
        return 1

    print(
        f"benchmarking {len(pdfs)} PDFs (languages={settings.ocr_languages}, "
        f"conf threshold={settings.ocr_confidence_threshold}, "
        f"text-layer min chars/page={settings.text_layer_min_chars_per_page}, "
        f"force_scans={args.force_scans})"
    )
    total_start = time.perf_counter()
    results: list[SampleResult] = []
    for index, pdf_path in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] {pdf_path.name} ...", flush=True)
        results.append(bench_document(pdf_path, index, settings, force_scans=args.force_scans))

    print_full_table(results)
    print(f"\ntotal wall clock: {time.perf_counter() - total_start:.1f}s")

    if args.json:
        args.json.write_text(
            json.dumps([dataclasses.asdict(r) for r in results], indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown(results, settings), encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
