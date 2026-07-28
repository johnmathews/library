"""Tests for scripts/check_engine_skips.py (the real-engine non-skip floor).

`scripts/` is not an importable package, so load the module by file path.

One test per hole rather than one happy path: the gate exists to catch a skip
that pytest reports as success, so "returns green" is the failure mode to pin
down, not the behaviour to confirm.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_engine_skips.py"
_spec = importlib.util.spec_from_file_location("check_engine_skips", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_engine_skips = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_engine_skips)


def _report(path: Path, body: str) -> Path:
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites>{body}</testsuites>')
    return path


def _skip_case(classname: str, name: str, message: str) -> str:
    return (
        f'<testsuite name="pytest"><testcase classname="{classname}" name="{name}">'
        f'<skipped type="pytest.skip" message="{message}">'
        f"/repo/tests/test_ocr_real.py:83: {message}"
        f"</skipped></testcase></testsuite>"
    )


class TestOffendingSkips:
    def test_rapidocr_skip_is_reported(self, tmp_path: Path) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case(
                "tests.test_ocr_real.TestRealRapidOcr",
                "test_photo_roundtrip",
                "rapidocr engine unavailable: Invalid OCR configuration.",
            ),
        )

        offenders = check_engine_skips.find_offending_skips(report, ("rapidocr", "tesseract"))

        assert len(offenders) == 1
        test_id, reason = offenders[0]
        assert test_id == "tests.test_ocr_real.TestRealRapidOcr::test_photo_roundtrip"
        assert "Invalid OCR configuration." in reason

    def test_tesseract_skip_is_reported(self, tmp_path: Path) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case(
                "tests.test_ocr_real.TestRealTesseract",
                "test_image_pdf_roundtrip",
                "tesseract binary not installed",
            ),
        )

        offenders = check_engine_skips.find_offending_skips(report, ("rapidocr", "tesseract"))

        assert [t for t, _ in offenders] == [
            "tests.test_ocr_real.TestRealTesseract::test_image_pdf_roundtrip"
        ]

    def test_matching_is_case_insensitive(self, tmp_path: Path) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case("tests.test_ocr_real.T", "t", "RapidOCR engine unavailable: boom"),
        )

        assert check_engine_skips.find_offending_skips(report, ("rapidocr",))

    def test_reason_only_in_element_text_is_still_caught(self, tmp_path: Path) -> None:
        """A skip with no @message must not empty the haystack."""
        report = _report(
            tmp_path / "r.xml",
            '<testsuite name="pytest"><testcase classname="c" name="n">'
            "<skipped>could not init rapidocr</skipped></testcase></testsuite>",
        )

        assert check_engine_skips.find_offending_skips(report, ("rapidocr",))

    def test_unrelated_skip_is_ignored(self, tmp_path: Path) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case("tests.test_docker", "test_needs_docker", "docker not available"),
        )

        assert check_engine_skips.find_offending_skips(report, ("rapidocr", "tesseract")) == []

    def test_engine_named_only_in_a_passing_test_is_ignored(self, tmp_path: Path) -> None:
        """The gate keys on skip reasons, not on test names mentioning an engine."""
        report = _report(
            tmp_path / "r.xml",
            '<testsuite name="pytest"><testcase classname="tests.test_ocr_router" '
            'name="test_rapidocr_wins_the_gate"/></testsuite>',
        )

        assert check_engine_skips.find_offending_skips(report, ("rapidocr",)) == []

    def test_engine_filter_is_respected(self, tmp_path: Path) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case("c", "n", "tesseract binary not installed"),
        )

        assert check_engine_skips.find_offending_skips(report, ("rapidocr",)) == []
        assert check_engine_skips.find_offending_skips(report, ("tesseract",))


class TestUnusableReport:
    """Cannot-look and nothing-found must never share an exit code."""

    def test_missing_report_raises(self, tmp_path: Path) -> None:
        with pytest.raises(check_engine_skips.ReportError, match="does not exist"):
            check_engine_skips.find_offending_skips(tmp_path / "absent.xml", ("rapidocr",))

    def test_unparseable_report_raises(self, tmp_path: Path) -> None:
        broken = tmp_path / "r.xml"
        broken.write_text("<testsuites><testcase not closed")

        with pytest.raises(check_engine_skips.ReportError, match="not parseable"):
            check_engine_skips.find_offending_skips(broken, ("rapidocr",))

    def test_doctype_is_refused(self, tmp_path: Path) -> None:
        bomb = tmp_path / "r.xml"
        bomb.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>\n'
            '<testsuites><testsuite name="p"><testcase classname="c" name="n">'
            '<skipped message="&lol2;"/></testcase></testsuite></testsuites>'
        )

        with pytest.raises(check_engine_skips.ReportError, match="DOCTYPE"):
            check_engine_skips.find_offending_skips(bomb, ("rapidocr",))


class TestExitCodes:
    def test_clean_report_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        report = _report(
            tmp_path / "r.xml",
            '<testsuite name="pytest"><testcase classname="c" name="n"/></testsuite>',
        )

        assert check_engine_skips.main([str(report)]) == 0
        assert "ok:" in capsys.readouterr().out

    def test_offending_report_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case("c", "n", "rapidocr engine unavailable: boom"),
        )

        assert check_engine_skips.main([str(report)]) == 1
        assert "rapidocr engine unavailable: boom" in capsys.readouterr().err

    def test_unusable_report_exits_two(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """Distinct from 1, so a broken gate is not read as a caught skip."""
        assert check_engine_skips.main([str(tmp_path / "absent.xml")]) == 2
        assert "does not exist" in capsys.readouterr().err

    def test_engine_flag_overrides_the_default_pair(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        report = _report(
            tmp_path / "r.xml",
            _skip_case("c", "n", "tesseract binary not installed"),
        )

        assert check_engine_skips.main([str(report), "--engine", "rapidocr"]) == 0
        assert check_engine_skips.main([str(report)]) == 1
        assert "tesseract binary not installed" in capsys.readouterr().err
