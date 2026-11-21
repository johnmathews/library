"""The app's tokens.css is generated from the design system's tokens.json.

`frontend/src/assets/tokens.css` is what the app paints with;
`brand/design-system/project/tokens.json` is what the design system (and its
contrast gate) says. The file is generated, so the only way they drift is
someone editing one side and not regenerating. This test regenerates into a
temporary file and compares, so a stale copy fails CI instead of shipping a
colour the gate never checked.

To fix a failure, regenerate (the command is in the assertion message).
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SYSTEM = _ROOT / "brand" / "design-system"
_APP_TOKENS = _ROOT / "frontend" / "src" / "assets" / "tokens.css"
_REGENERATE = [
    sys.executable,
    str(_ROOT / "scripts" / "design_preview.py"),
    str(_SYSTEM),
    "--tokens-css",
]
_FONT_BASE = ["--font-base", "./fonts"]


def test_app_tokens_css_matches_tokens_json(tmp_path: Path) -> None:
    fresh = tmp_path / "tokens.css"
    subprocess.run([*_REGENERATE, str(fresh), *_FONT_BASE], check=True, capture_output=True)
    command = (
        "python3 scripts/design_preview.py brand/design-system"
        " --tokens-css frontend/src/assets/tokens.css --font-base ./fonts"
    )
    assert _APP_TOKENS.read_text() == fresh.read_text(), f"tokens.css is stale; run: {command}"
