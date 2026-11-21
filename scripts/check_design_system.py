#!/usr/bin/env python3
"""Gate for a project's design system source (the files behind its claude.ai Design System artifact).

Copy this script into the project (e.g. scripts/check_design_system.py) and run it in CI, so the
gate lives with the code it guards. It checks:

- tokens.json has the list shape the artifact page reads, unique names, #rrggbb colours, and a
  usage note on every token;
- every colour pairing in the contrast pairs meets its WCAG ratio in every theme;
- no component preview or asset text uses a forbidden word (optional);
- every component folder has a preview with its @dsCard marker on line 1, and a README
  (the cover is exempt: a README beside it would turn it into an ordinary component);
- the email preview (optional) uses inline styles only and gives every image alt text.

Project settings live in <system-dir>/checks.json (all keys optional):

    {
      "required_components": ["Button", "Cover"],
      "extra_contrast_pairs": [["free-ink", "free-surface", 4.5]],
      "forbidden_words": ["camera"],
      "forbidden_words_js": {"file": "site/src/copy-rules.js", "array": "FORBIDDEN_STEMS"},
      "email_preview": "components/EmailLayout/preview.html"
    }

Paths in forbidden_words_js are relative to the repository root (the --repo argument, default
the current directory).

When the design-system skill is installed on this machine, a run also compares this copy's
SCRIPT_VERSION with the skill's and prints a note (to stderr, never changing the exit code) when
they differ. `--version` prints the version.

Usage: python3 check_design_system.py <system-dir> [--complete] [--repo <root>]
       python3 check_design_system.py --version
       python3 check_design_system.py --selftest
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Bump on every change to this file: ci/lint-skill-versions.sh (codespaces-setup) fails a change
# that doesn't. Projects copy this script, and the number is how a copy knows it has fallen behind.
SCRIPT_VERSION = 1
SCRIPT_NAME = "check_design_system.py"  # the skill's copy is looked up by this name

# >>> copy-version (keep byte-identical in check_design_system.py and design_preview.py;
# ci/lint-skill-versions.sh checks it)
VERSION_LINE = re.compile(r"^SCRIPT_VERSION = (\d+)", re.M)


def installed_skill_dir() -> Path | None:
    """The design-system skill on this machine, if there is one (CI usually has none)."""
    candidates = [os.environ.get("DESIGN_SYSTEM_SKILL_DIR")]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(str(Path(config) / "skills/design-system"))
    candidates.append(str(Path.home() / ".claude/skills/design-system"))
    return next((Path(c) for c in candidates if c and (Path(c) / "SKILL.md").is_file()), None)


def copy_note(this_file: Path, skill_dir: Path | None) -> str | None:
    """Compare this copy with the skill's copy of the same script; None when there is nothing to say.

    The skill's copy is found by SCRIPT_NAME, not this file's name, so a project that renamed its
    copy still gets the note. Never raises: the note is advice and must not break the run.
    """
    if not skill_dir:
        return None
    skill_copy = skill_dir / "scripts" / SCRIPT_NAME
    try:
        if not skill_copy.is_file() or skill_copy.resolve() == this_file.resolve():
            return None
        theirs_text = skill_copy.read_text(encoding="utf-8")
        same_bytes = skill_copy.read_bytes() == this_file.read_bytes()
    except (OSError, UnicodeDecodeError):
        return None
    found = VERSION_LINE.search(theirs_text)
    theirs = int(found.group(1)) if found else 0
    if theirs > SCRIPT_VERSION:
        return (f"note: {this_file.name} here is version {SCRIPT_VERSION}; the design-system skill has "
                f"{theirs}. Update this copy: cp {skill_copy} {this_file}")
    if theirs < SCRIPT_VERSION:
        return (f"note: {this_file.name} here is version {SCRIPT_VERSION}, newer than the installed "
                f"design-system skill ({theirs}). Update the skill: `codespaces-update claude`, or "
                f"`bash scripts/17-claude-skills.sh` in codespaces-setup.")
    if not same_bytes:
        return (f"note: {this_file.name} matches the skill's version ({theirs}) but not its contents: "
                f"edited here, or changed in the skill without a version bump. Compare with {skill_copy}")
    return None
# <<< copy-version


NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MARKER = re.compile(r"^<!-- @dsCard\b[^>]*-->$")
TEXT_SUFFIXES = {".svg", ".md", ".txt", ".json", ".html"}
NO_README = {"Cover"}
SKIP_FAMILIES = {"type", "name", "version", "meta"}

# The standard semantic colour roles (references/tokens.md) and the pairings every component
# paints with them. A project adds its own accents through checks.json extra_contrast_pairs.
STANDARD_PAIRS: list[tuple[str, str, float]] = [
    ("ink", "canvas", 4.5), ("ink", "panel", 4.5), ("ink", "tile", 4.5),
    ("ink-secondary", "canvas", 4.5), ("ink-secondary", "panel", 4.5), ("ink-secondary", "tile", 4.5),
    ("on-ink", "ink", 4.5),
    ("error-ink", "canvas", 4.5), ("error-ink", "panel", 4.5),
    ("control-border", "canvas", 3.0), ("control-border", "panel", 3.0),
    ("focus", "canvas", 3.0), ("focus", "panel", 3.0),
]  # fmt: skip


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    lighter, darker = sorted((_luminance(fg.lower()), _luminance(bg.lower())), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _themes(tokens: dict) -> list[str]:
    return [t["id"] for t in tokens.get("color", {}).get("themes", [])] or ["light"]


def _values(token: dict, themes: list[str]) -> dict[str, object]:
    value = token.get("value")
    if isinstance(value, dict):
        return {theme: value.get(theme, value.get(themes[0])) for theme in themes}
    return dict.fromkeys(themes, value)


def token_problems(tokens: dict) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    themes = _themes(tokens)
    for family, body in tokens.items():
        if family in SKIP_FAMILIES:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("tokens"), list):
            problems.append(f"{family}: not a {{'tokens': [...]}} list (the page cannot read a name-to-value map)")
            continue
        for token in body["tokens"]:
            name = token.get("name", "")
            if not NAME.match(name):
                problems.append(f"{family}: bad name {name!r}")
            if name in seen:
                problems.append(f"{family}: duplicate name {name!r}")
            seen.add(name)
            if not str(token.get("usage", "")).strip():
                problems.append(f"{family}: {name!r} has no usage note")
            if family == "color":
                for theme, value in _values(token, themes).items():
                    if not isinstance(value, str) or not HEX.match(value):
                        problems.append(f"color: {name!r} in {theme} should be #rrggbb, got {value!r}")
    return problems


def contrast_problems(tokens: dict, pairs: list[tuple[str, str, float]]) -> list[str]:
    themes = _themes(tokens)
    colours = {t["name"]: _values(t, themes) for t in tokens.get("color", {}).get("tokens", [])}
    problems: list[str] = []
    for fg, bg, minimum in pairs:
        if fg not in colours or bg not in colours:
            problems.append(f"{fg} on {bg}: token missing")
            continue
        for theme in themes:
            a, b = str(colours[fg][theme]), str(colours[bg][theme])
            if not (HEX.match(a) and HEX.match(b)):
                continue  # token_problems already reports the bad value
            ratio = contrast_ratio(a, b)
            if ratio < minimum:
                problems.append(f"{fg} on {bg} in {theme}: {ratio:.2f}:1, needs {minimum}:1")
    return problems


def forbidden_from_js(path: Path, array: str) -> list[str]:
    match = re.search(rf"{re.escape(array)}\s*=\s*\[(.*?)\]", path.read_text(), re.S)
    if not match:
        raise ValueError(f"{path}: no {array} array")
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def copy_problems(project: Path, words: list[str]) -> list[str]:
    files = sorted(project.glob("components/*/preview.html"))
    files += sorted(p for p in project.glob("assets/**/*") if p.suffix in TEXT_SUFFIXES)
    problems: list[str] = []
    for path in files:
        text = path.read_text().lower()
        problems += [f"{path.relative_to(project)}: uses '{w}'" for w in words if w.lower() in text]
    return problems


def inventory_problems(project: Path, required: list[str], complete: bool) -> list[str]:
    problems: list[str] = []
    folders = sorted(p for p in project.glob("components/*") if p.is_dir() and p.name != "lib")
    for folder in folders:
        name = f"components/{folder.name}"
        preview = folder / "preview.html"
        if not preview.exists():
            problems.append(f"{name}: no preview.html")
        elif not MARKER.match(preview.read_text().split("\n", 1)[0]):
            problems.append(f"{name}: preview.html line 1 is not an @dsCard marker")
        if folder.name not in NO_README and not (folder / "README.md").exists():
            problems.append(f"{name}: no README.md")
        if folder.name in NO_README and (folder / "README.md").exists():
            problems.append(f"{name}: a README.md here turns the cover into an ordinary component")
    if complete:
        present = {p.name for p in folders}
        problems += [f"components/{c}: missing" for c in required if c not in present]
    return problems


def email_problems(preview: Path) -> list[str]:
    html = preview.read_text()
    problems = [f"{preview.name}: has <{tag}>" for tag in ("style", "link", "script") if f"<{tag}" in html.lower()]
    if "var(--" in html:
        problems.append(f"{preview.name}: uses var(--…); email clients don't read CSS variables")
    for img in re.findall(r"<img\b[^>]*>", html, re.I):
        if not re.search(r'\balt="[^"]+"', img):
            problems.append(f"{preview.name}: image without alt text: {img}")
    return problems


def run(system: Path, complete: bool, repo: Path) -> list[str]:
    project = system / "project"
    config_path = system / "checks.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    tokens = json.loads((project / "tokens.json").read_text())
    pairs = STANDARD_PAIRS + [tuple(p) for p in config.get("extra_contrast_pairs", [])]
    problems = token_problems(tokens) + contrast_problems(tokens, pairs)
    words = list(config.get("forbidden_words", []))
    if js := config.get("forbidden_words_js"):
        words += forbidden_from_js(repo / js["file"], js.get("array", "FORBIDDEN_STEMS"))
    if words:
        problems += copy_problems(project, words)
    problems += inventory_problems(project, config.get("required_components", ["Cover"]), complete)
    if email := config.get("email_preview"):
        if (project / email).exists():
            problems += email_problems(project / email)
        elif complete:
            problems.append(f"{email}: missing")
    return problems


def _selftest() -> int:
    good = {
        "color": {
            "themes": [{"id": "light"}, {"id": "dark"}],
            "tokens": [
                {"name": n, "value": {"light": lv, "dark": dv}, "usage": "u"}
                for n, lv, dv in [
                    ("canvas", "#fbfbfa", "#161615"), ("panel", "#ffffff", "#1f1f1e"), ("tile", "#f0f0ee", "#2b2b29"),
                    ("ink", "#1d1d1f", "#f5f5f3"), ("ink-secondary", "#6c6c71", "#a1a1a6"),
                    ("on-ink", "#ffffff", "#161615"), ("error-ink", "#b3261e", "#f2b8b5"),
                    ("control-border", "#86868b", "#7c7c80"), ("focus", "#1d1d1f", "#f5f5f3"),
                ]
            ],
        },
        "spacing": {"tokens": [{"name": "space-4", "value": "16px", "usage": "u"}]},
    }  # fmt: skip
    checks: list[tuple[str, bool]] = [
        ("WCAG ratio black on white is 21", round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0),
        ("WCAG ratio #777 on white is 4.48", round(contrast_ratio("#777777", "#ffffff"), 2) == 4.48),
        ("good tokens pass", token_problems(good) == [] and contrast_problems(good, STANDARD_PAIRS) == []),
        ("a map-shaped family is rejected", bool(token_problems({"color": {"brand": {"$value": "#f00"}}}))),
        ("a named colour and a missing usage are both caught", len(token_problems(
            {"color": {"themes": [{"id": "light"}], "tokens": [{"name": "a", "value": "red", "usage": ""}]}})) == 2),
    ]  # fmt: skip
    weak = json.loads(json.dumps(good))
    weak["color"]["tokens"][4]["value"]["dark"] = "#3a3a3a"
    checks.append(("a failing dark pair is named", any("dark" in p for p in contrast_problems(weak, STANDARD_PAIRS))))
    with tempfile.TemporaryDirectory() as tmp:
        system = Path(tmp)
        project = system / "project"
        (project / "components/Button").mkdir(parents=True)
        (project / "tokens.json").write_text(json.dumps(good))
        (project / "components/Button/preview.html").write_text('<!-- @dsCard group="Basics" -->\n<p>Slimme Camera</p>')
        (project / "components/Button/README.md").write_text("A button.")
        (system / "checks.json").write_text(json.dumps({"required_components": ["Button", "Cover"],
                                                         "forbidden_words": ["camera"]}))  # fmt: skip
        problems = run(system, complete=True, repo=system)
        checks.append(("a forbidden word in a preview is caught", any("camera" in p for p in problems)))
        checks.append(("a missing required component is caught", "components/Cover: missing" in problems))
        (project / "components/Cover").mkdir()
        (project / "components/Cover/preview.html").write_text("<svg></svg>")
        (project / "components/Cover/README.md").write_text("x")
        problems = run(system, complete=True, repo=system)
        checks.append(("a cover without marker, with README, is caught twice",
                       sum("components/Cover" in p for p in problems) == 2))  # fmt: skip
        email = project / "email.html"
        email.write_text('<style>p{}</style><p style="color:var(--ink)">x</p><img src="a.png">')
        checks.append(("email: <style>, var() and a missing alt are caught", len(email_problems(email)) == 3))
    with tempfile.TemporaryDirectory() as tmp:
        here = Path(tmp) / "project" / "renamed_copy.py"  # a renamed copy must still compare
        skill = Path(tmp) / "skill"
        (skill / "scripts").mkdir(parents=True)
        here.parent.mkdir()
        source = Path(__file__).read_text(encoding="utf-8")
        here.write_text(source, encoding="utf-8")
        theirs = skill / "scripts" / SCRIPT_NAME

        def note_with(text: str) -> str:
            theirs.write_text(text, encoding="utf-8")
            return copy_note(here, skill) or ""

        def bumped(n: int) -> str:
            return source.replace(f"SCRIPT_VERSION = {SCRIPT_VERSION}", f"SCRIPT_VERSION = {n}", 1)

        checks += [
            ("versions: no installed skill, no note", copy_note(here, None) is None),
            ("versions: an identical copy, no note", note_with(source) == ""),
            ("versions: the skill is newer, say how to update the copy (renamed copy too)",
             "Update this copy: cp" in note_with(bumped(SCRIPT_VERSION + 1))),
            ("versions: the copy is newer, say how to update the skill",
             "Update the skill" in note_with(bumped(SCRIPT_VERSION - 1))),
            ("versions: same number, different bytes, flagged", "not its contents" in note_with(source + "#\n")),
            ("versions: the skill's own copy never compares with itself", copy_note(theirs, skill) is None),
        ]  # fmt: skip
        theirs.unlink()
        theirs.mkdir()  # unreadable as a file: the note is skipped, never raised
        checks.append(("versions: an unreadable skill copy gives no note and no crash", copy_note(here, skill) is None))
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return _selftest()
    if argv[:1] == ["--version"]:
        print(f"{Path(__file__).name} {SCRIPT_VERSION}")
        return 0
    if note := copy_note(Path(__file__), installed_skill_dir()):
        print(note, file=sys.stderr)
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().splitlines()[-3].strip())
        return 2
    repo = Path(argv[argv.index("--repo") + 1]) if "--repo" in argv else Path(".")
    problems = run(Path(argv[0]), complete="--complete" in argv, repo=repo)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
