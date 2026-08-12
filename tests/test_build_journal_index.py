"""Tests for scripts/build_journal_index.py (the generated journal index).

`scripts/` is not an importable package, so load the module by file path.

The contract under test is the same one `ruff format --check` has: the file on
disk must equal what the generator would write. The failure that matters is a
`--check` that passes on a hand-edited index — then the index drifts from the
entries it indexes while still looking authoritative.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_journal_index.py"
_spec = importlib.util.spec_from_file_location("build_journal_index", _SCRIPT)
assert _spec is not None and _spec.loader is not None
build_journal_index = importlib.util.module_from_spec(_spec)
sys.modules["build_journal_index"] = build_journal_index
_spec.loader.exec_module(build_journal_index)

Entry = build_journal_index.Entry
JournalError = build_journal_index.JournalError


class TestParsing:
    def test_filename_and_h1_become_a_row(self) -> None:
        entry = build_journal_index.parse_entry(
            "260610-project-inception.md", "# Project inception\n\nBody.\n"
        )
        assert entry == Entry(
            filename="260610-project-inception.md",
            day=date(2026, 6, 10),
            title="Project inception",
        )

    def test_a_dotted_slug_is_allowed(self) -> None:
        """`260611-v0.1.0-build-complete.md` is a real entry in this repo."""
        entry = build_journal_index.parse_entry(
            "260611-v0.1.0-build-complete.md", "# v0.1.0 build complete\n"
        )
        assert entry.day == date(2026, 6, 11)

    def test_a_title_with_markup_survives(self) -> None:
        entry = build_journal_index.parse_entry(
            "260729-x.md", "# The e2e job that passed *without* running\n"
        )
        assert entry.title == "The e2e job that passed *without* running"

    @pytest.mark.parametrize(
        "filename",
        ["no-date-here.md", "2606-short.md", "260610_underscore.md", "260610.md"],
    )
    def test_a_misnamed_entry_is_an_error(self, filename: str) -> None:
        with pytest.raises(JournalError, match="convention"):
            build_journal_index.parse_entry(filename, "# Title\n")

    def test_an_impossible_date_is_an_error(self) -> None:
        with pytest.raises(JournalError, match="not a real date"):
            build_journal_index.parse_entry("261345-bad.md", "# Title\n")

    def test_an_entry_without_an_h1_is_an_error_not_a_skip(self) -> None:
        """Skipping would produce an index that looks complete and is not."""
        with pytest.raises(JournalError, match="no H1"):
            build_journal_index.parse_entry("260610-x.md", "No heading here.\n")


class TestRendering:
    ENTRIES: ClassVar[list] = [
        Entry("260610-a.md", date(2026, 6, 10), "First"),
        Entry("260715-b.md", date(2026, 7, 15), "Second"),
        Entry("260716-c.md", date(2026, 7, 16), "Third"),
    ]

    def test_newest_first_and_grouped_by_month(self) -> None:
        rendered = build_journal_index.render_index(self.ENTRIES)
        assert rendered.index("## July 2026") < rendered.index("## June 2026")
        assert rendered.index("Third") < rendered.index("Second") < rendered.index("First")

    def test_every_entry_gets_exactly_one_row(self) -> None:
        rendered = build_journal_index.render_index(self.ENTRIES)
        assert rendered.count("\n- **") == len(self.ENTRIES)
        assert "3 entries." in rendered

    def test_rows_link_the_real_filename(self) -> None:
        rendered = build_journal_index.render_index(self.ENTRIES)
        assert "[Third](260716-c.md)" in rendered

    def test_it_says_it_is_generated(self) -> None:
        """A reader who edits it by hand should be told not to, in the file."""
        assert "generated" in build_journal_index.render_index(self.ENTRIES)

    def test_it_is_deterministic(self) -> None:
        first = build_journal_index.render_index(self.ENTRIES)
        second = build_journal_index.render_index(list(reversed(self.ENTRIES)))
        assert first == second

    def test_a_month_heading_is_preceded_by_a_blank_line(self) -> None:
        lines = build_journal_index.render_index(self.ENTRIES).splitlines()
        for index, line in enumerate(lines):
            if line.startswith("## ") and index > 0:
                assert lines[index - 1] == "", f"no blank line before {line!r}"


class TestCheckMode:
    def test_check_passes_on_the_committed_index(self) -> None:
        """The gate itself: the repo's index must be current right now."""
        assert build_journal_index.main(["--check"]) == 0

    def test_check_reds_on_a_hand_edited_index(self, tmp_path: Path, monkeypatch) -> None:
        journal = tmp_path / "journal"
        journal.mkdir()
        (journal / "260610-a.md").write_text("# First\n", encoding="utf-8")
        index = journal / "README.md"
        monkeypatch.setattr(build_journal_index, "JOURNAL_DIR", journal)
        monkeypatch.setattr(build_journal_index, "INDEX_PATH", index)
        monkeypatch.setattr(build_journal_index, "REPO_ROOT", tmp_path)

        assert build_journal_index.main([]) == 0
        assert build_journal_index.main(["--check"]) == 0

        index.write_text(index.read_text(encoding="utf-8") + "- hand edit\n", encoding="utf-8")
        assert build_journal_index.main(["--check"]) == 1

    def test_a_new_entry_without_regenerating_reds(self, tmp_path: Path, monkeypatch) -> None:
        """The contract that makes the index stay true."""
        journal = tmp_path / "journal"
        journal.mkdir()
        (journal / "260610-a.md").write_text("# First\n", encoding="utf-8")
        index = journal / "README.md"
        monkeypatch.setattr(build_journal_index, "JOURNAL_DIR", journal)
        monkeypatch.setattr(build_journal_index, "INDEX_PATH", index)
        monkeypatch.setattr(build_journal_index, "REPO_ROOT", tmp_path)
        assert build_journal_index.main([]) == 0

        (journal / "260611-b.md").write_text("# Second\n", encoding="utf-8")
        assert build_journal_index.main(["--check"]) == 1

    def test_an_empty_journal_cannot_check(self, tmp_path: Path, monkeypatch) -> None:
        """ "Cannot check" must not share an exit code with "nothing wrong"."""
        journal = tmp_path / "journal"
        journal.mkdir()
        monkeypatch.setattr(build_journal_index, "JOURNAL_DIR", journal)
        monkeypatch.setattr(build_journal_index, "INDEX_PATH", journal / "README.md")
        assert build_journal_index.main(["--check"]) == 2

    def test_a_malformed_entry_cannot_check(self, tmp_path: Path, monkeypatch) -> None:
        journal = tmp_path / "journal"
        journal.mkdir()
        (journal / "nope.md").write_text("# X\n", encoding="utf-8")
        monkeypatch.setattr(build_journal_index, "JOURNAL_DIR", journal)
        monkeypatch.setattr(build_journal_index, "INDEX_PATH", journal / "README.md")
        assert build_journal_index.main(["--check"]) == 2
