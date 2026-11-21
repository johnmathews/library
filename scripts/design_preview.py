#!/usr/bin/env python3
"""Build a local, static preview of a design system, or compile its tokens.css for an app.

The claude.ai Design System artifact is the published view. This writes a plain HTML gallery
from the same source, so the system can be browsed offline: the colour tokens in every theme,
then every component preview per theme side by side, with a switch to phone width.

tokens.css is compiled the way the artifact type compiles it (its format.md: "tokens.css as
compiled"), so previews render the same here. `--tokens-css` writes only that file, for an app
to import before Tailwind's `@theme inline` block (references/code.md). Its dark theme sits on a
`.dark` class by default: shadcn-vue and templates/a11y.spec.ts use it, Tailwind's `dark:`
variant follows it as templates/app-theme.css configures it, and Tailwind is the likely case. An app that switches themes another way passes
`--dark-selector '[data-theme="dark"]'` (or its own selector) and changes the spec's DARK to
match. The gallery keeps `[data-theme]`, which is what the artifact itself uses.

Group order on the gallery comes from <system-dir>/checks.json "group_order" if present,
otherwise the order groups first appear in (the cover always first).

Stdlib only. Copy it into the project's scripts/ beside check_design_system.py. Like the gate,
it notes (on stderr) when this copy's SCRIPT_VERSION differs from the installed skill's.

Usage: python3 design_preview.py <system-dir>                 # writes <system-dir>/preview/
       python3 design_preview.py <system-dir> --tokens-css <out.css> [--dark-selector <css>] [--font-base <url>]
       python3 design_preview.py --version
       python3 design_preview.py --selftest
"""

import contextlib
import html
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Bump on every change to this file: ci/lint-skill-versions.sh (codespaces-setup) fails a change
# that doesn't. Projects copy this script, and the number is how a copy knows it has fallen behind.
SCRIPT_VERSION = 1
SCRIPT_NAME = "design_preview.py"  # the skill's copy is looked up by this name

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


MARKER = re.compile(r"^<!-- @dsCard\b([^>]*)-->\n?")
# --tokens-css only. Keep it equal to the class templates/a11y.spec.ts toggles, or the dark audit
# runs against the light theme and passes.
DEFAULT_DARK_SELECTOR = ".dark"


PAGE_CSS = """\
body {
  margin: 0; padding: var(--space-8) var(--space-12);
  background: var(--canvas); color: var(--ink); font-family: var(--font-sans);
}
header { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); flex-wrap: wrap; }
h1 { margin: 0; font: 600 40px/44px var(--font-sans); letter-spacing: -0.02em; }
h2 { margin: var(--space-12) 0 var(--space-4); font: 600 28px/32px var(--font-sans); }
.card {
  background: var(--panel); border-radius: var(--radius-lg);
  padding: var(--space-6); margin-bottom: var(--space-6);
}
.card h3 { margin: 0; font: 600 21px/26px var(--font-sans); }
.card p, .lead { margin: var(--space-1) 0 var(--space-4); color: var(--ink-secondary); }
.frames { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
iframe { width: 100%; border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--canvas); }
body.phone .frames { grid-template-columns: repeat(2, 375px); overflow-x: auto; }
#Cover .frames { grid-template-columns: 1fr; overflow-x: auto; }
#Cover iframe { width: 960px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td {
  text-align: left; vertical-align: middle;
  padding: var(--space-2) var(--space-3) var(--space-2) 0; border-bottom: 1px solid var(--line);
}
.swatch {
  display: inline-block; width: 18px; height: 18px; border-radius: 50%;
  border: 1px solid var(--line); vertical-align: middle; margin-right: var(--space-2);
}
button {
  font: 500 15px/20px var(--font-sans); padding: var(--space-2) var(--space-4); cursor: pointer;
  border-radius: var(--radius-full); border: 1px solid var(--control-border);
  background: transparent; color: var(--ink);
}
"""


def _value(token: dict, theme: str, first: str) -> str:
    value = token["value"]
    return value if isinstance(value, str) else value.get(theme, value[first])


def tokens_css(tokens: dict, font_base: str, dark_selector: str | None = None) -> str:
    themes = [t["id"] for t in tokens["color"]["themes"]]
    themed = tokens["color"]["tokens"] + tokens.get("shadow", {}).get("tokens", [])
    lines: list[str] = []
    for theme in themes:
        later = dark_selector if dark_selector and theme == "dark" else f'[data-theme="{theme}"]'
        selector = f':root, [data-theme="{theme}"]' if theme == themes[0] else later
        body = "".join(f"  --{t['name']}: {_value(t, theme, themes[0])};\n" for t in themed)
        lines.append(f"{selector} {{\n{body}}}")
    scales = [t for family in ("spacing", "radius") for t in tokens.get(family, {}).get("tokens", [])]
    body = "".join(f"  --{t['name']}: {t['value']};\n" for t in scales)
    body += "".join(f"  --font-{key}: {stack};\n" for key, stack in tokens["type"]["families"].items())
    lines.append(f":root {{\n{body}}}")
    for group in tokens["type"]["groups"]:
        for style in group["styles"]:
            family = style.get("family", group["family"])
            lines.append(
                f".{style['name']} {{ font-family: var(--font-{family}); font-size: {style['fontSize']}; "
                f"line-height: {style['lineHeight']}; font-weight: {style['fontWeight']}; "
                f"letter-spacing: {style.get('letterSpacing', '0')}; }}"
            )
    for font in tokens["type"].get("fonts", []):
        if not font.get("file"):
            continue  # a hosted (e.g. Google) face: the app loads it, tokens only name the family
        name = Path(font["file"]).name
        lines.append(
            f'@font-face {{ font-family: "{font["family"]}"; src: url("{font_base}/{name}") format("woff2"); '
            f"font-weight: {font['weight']}; font-style: {font.get('style', 'normal')}; font-display: swap; }}"
        )
    return "\n".join(lines) + "\n"


def card_height(preview: str) -> int:
    match = re.search(r"height=(\d+)", MARKER.match(preview).group(1)) if MARKER.match(preview) else None
    return int(match.group(1)) if match else 120


def _group(preview: str) -> str:
    match = MARKER.match(preview)
    found = re.search(r'group="([^"]+)"', match.group(1)) if match else None
    return found.group(1) if found else "Cover"


def _summary(readme: Path) -> str:
    if not readme.exists():
        return ""
    first = readme.read_text().strip().split("\n\n", 1)[0]
    return first.split(". ", 1)[0].rstrip(".") + "."


def _themed_page(preview: str, theme: str) -> str:
    # The marker must go: a comment before <!doctype> would put the page in quirks mode.
    page = MARKER.sub("", preview, count=1)
    page = page.replace("<html", f'<html data-theme="{theme}"', 1)
    return page.replace("<head>", '<head>\n<link rel="stylesheet" href="../tokens.css">', 1)


def _framed_page(preview: str, theme: str) -> str:
    return _themed_page(preview, theme).replace("<head>", '<head>\n<base href="components/">', 1)


def _colour_rows(tokens: dict) -> str:
    themes = [t["id"] for t in tokens["color"]["themes"]]
    rows = []
    for token in tokens["color"]["tokens"]:
        cells = "".join(
            f'<td><span class="swatch" style="background:{_value(token, th, themes[0])}"></span>'
            f"<code>{_value(token, th, themes[0])}</code></td>"
            for th in themes
        )
        rows.append(f"<tr><td><code>{token['name']}</code></td>{cells}<td>{html.escape(token['usage'])}</td></tr>")
    head = "".join(f"<th>{th.title()}</th>" for th in themes)
    return f"<table><thead><tr><th>Token</th>{head}<th>Use</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def build(project: Path, out: Path, group_order: list[str] | None = None) -> Path:
    tokens = json.loads((project / "tokens.json").read_text())
    title = html.escape(str(tokens.get("name", "Design system")))
    themes = [t["id"] for t in tokens["color"]["themes"]]
    (out / "components").mkdir(parents=True, exist_ok=True)
    fonts = Path("..") / project.relative_to(project.parent) / "fonts"
    (out / "tokens.css").write_text(tokens_css(tokens, font_base=fonts.as_posix()))

    cards: dict[str, list[str]] = {}
    for folder in sorted(p for p in (project / "components").iterdir() if (p / "preview.html").exists()):
        preview = (folder / "preview.html").read_text()
        for theme in themes:
            (out / "components" / f"{folder.name}.{theme}.html").write_text(_themed_page(preview, theme))
        # Inline (srcdoc) frames share the index's origin, even on file://, so the
        # index can size each one to its content. <base> keeps relative links working.
        frames = "".join(
            f'<iframe srcdoc="{html.escape(_framed_page(preview, th))}" height="{card_height(preview)}" '
            f'title="{folder.name}, {th}" loading="lazy"></iframe>'
            for th in themes
        )
        summary = html.escape(_summary(folder / "README.md"))
        card = (
            f'<section class="card" id="{folder.name}"><h3>{folder.name}</h3><p>{summary}</p>'
            f'<div class="frames">{frames}</div></section>'
        )
        cards.setdefault(_group(preview), []).append(card)

    order = group_order or list(cards)  # dicts keep first-seen order
    groups = ["Cover"] + [g for g in order if g != "Cover"] + [g for g in cards if g not in order]
    body = "".join(f"<h2>{g}</h2>{''.join(cards[g])}" for g in groups if g in cards)
    page = f"""<!doctype html>
<html lang="en" data-theme="{themes[0]}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} design system — local preview</title>
<link rel="stylesheet" href="tokens.css">
<style>
{PAGE_CSS}</style>
</head>
<body>
<header><h1>{title}</h1><button type="button" id="width">Show at phone width</button></header>
<p class="lead">A local preview built from this repository's design system files.
The published system is the claude.ai Design System artifact.</p>
<h2>Colours</h2>
{_colour_rows(tokens)}
{body}
<script>
  const fit = (frame) => {{
    try {{ frame.style.height = frame.contentDocument.documentElement.scrollHeight + 'px' }} catch {{}}
  }}
  const frames = [...document.querySelectorAll('iframe')]
  frames.forEach((frame) => frame.addEventListener('load', () => fit(frame)))
  document.getElementById('width').addEventListener('click', (e) => {{
    const phone = document.body.classList.toggle('phone')
    e.target.textContent = phone ? 'Show at full width' : 'Show at phone width'
    requestAnimationFrame(() => frames.forEach(fit))
  }})
</script>
</body>
</html>
"""
    index = out / "index.html"
    index.write_text(page)
    return index


def _selftest() -> int:
    tokens = {
        "name": "Acme",
        "color": {"themes": [{"id": "light"}, {"id": "dark"}],
                  "tokens": [{"name": "ink", "value": {"light": "#111111", "dark": "#eeeeee"}, "usage": "Text."},
                             {"name": "canvas", "value": "#ffffff", "usage": "Page."}]},
        "type": {"fonts": [{"family": "Acme Sans", "file": "fonts/acme-400.woff2", "weight": "400"},
                           {"family": "Hosted", "weight": "400"}],
                 "families": {"sans": "\"Acme Sans\", system-ui, sans-serif"},
                 "groups": [{"name": "Text", "family": "sans",
                             "styles": [{"name": "body", "fontSize": "16px", "lineHeight": "24px", "fontWeight": 400}]}]},
        "spacing": {"tokens": [{"name": "space-4", "value": "16px", "usage": "Gaps."}]},
    }  # fmt: skip
    css = tokens_css(tokens, font_base="fonts")
    dark_css = tokens_css(tokens, font_base="fonts", dark_selector=".dark")
    checks: list[tuple[str, bool]] = [
        ("first theme sits on :root", ':root, [data-theme="light"] {' in css and "--ink: #111111;" in css),
        ("a later theme gets its own block", '[data-theme="dark"] {' in css and "--ink: #eeeeee;" in css),
        ("a plain value fills every theme", css.count("--canvas: #ffffff;") == 2),
        ("--dark-selector swaps the dark block's selector", ".dark {" in dark_css and '[data-theme="dark"]' not in dark_css),
        ("scales and families land on :root", "--space-4: 16px;" in css and "--font-sans:" in css),
        ("type styles become classes", ".body {" in css),
        ("only fonts with a file get @font-face", css.count("@font-face") == 1),
        ("the marker height is read", card_height('<!-- @dsCard group="A" height=230 -->\n') == 230),
        ("a preview without a marker defaults to 120", card_height("<p></p>") == 120),
    ]  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        (project / "components/Button").mkdir(parents=True)
        (project / "components/Cover").mkdir()
        (project / "tokens.json").write_text(json.dumps(tokens))
        (project / "components/Button/preview.html").write_text(
            '<!-- @dsCard group="Basics" height=90 -->\n<!doctype html><html><head></head><body>Go</body></html>')
        (project / "components/Button/README.md").write_text("The button. More text.")
        (project / "components/Cover/preview.html").write_text(
            "<!-- @dsCard height=288 -->\n<!doctype html><html><head></head><body>Acme</body></html>")
        index = build(project, Path(tmp) / "preview").read_text()
        page = (Path(tmp) / "preview/components/Button.dark.html").read_text()
        app_css = Path(tmp) / "app-tokens.css"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main([tmp, "--tokens-css", str(app_css)])
        app_css_text = app_css.read_text()
        checks.append(("--tokens-css defaults to the .dark class templates/a11y.spec.ts flips",
                       ".dark {" in app_css_text and '[data-theme="dark"]' not in app_css_text))  # fmt: skip
        checks += [
            ("the index names the system", "<h1>Acme</h1>" in index),
            ("components appear with their summary", 'id="Button"' in index and "The button." in index),
            ("the cover comes first", index.index('id="Cover"') < index.index('id="Button"')),
            ("frames are inlined so they can fit their content", 'srcdoc="' in index and "scrollHeight" in index),
            ("per-theme pages drop the marker and set the theme",
             page.startswith("<!doctype html>") and '<html data-theme="dark"' in page),
        ]  # fmt: skip
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
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return _selftest()
    if argv[:1] == ["--version"]:
        print(f"{Path(__file__).name} {SCRIPT_VERSION}")
        return 0
    if note := copy_note(Path(__file__), installed_skill_dir()):
        print(note, file=sys.stderr)
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().splitlines()[-4].strip())
        return 2
    system = Path(argv[0])
    project = system / "project"

    def option(flag: str) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else None

    if out := option("--tokens-css"):
        tokens = json.loads((project / "tokens.json").read_text())
        css = tokens_css(
            tokens,
            font_base=option("--font-base") or "fonts",
            dark_selector=option("--dark-selector") or DEFAULT_DARK_SELECTOR,
        )
        Path(out).write_text(f"/* {tokens.get('name', 'Design system')} — generated from tokens.json */\n{css}")
        print(out)
        return 0
    config_path = system / "checks.json"
    order = json.loads(config_path.read_text()).get("group_order") if config_path.exists() else None
    print(build(project, system / "preview", group_order=order))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
