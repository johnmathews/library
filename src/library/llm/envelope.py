"""Unwrap a JSON payload a model wrapped in prose or a markdown fence.

Shared because the same production incident has now happened twice, in two
unrelated callers: a model told to "return only JSON" returned it inside a
```` ```json ```` fence, ``json.loads`` raised on the raw text, and every
document in the run was silently left undecided. The first occurrence was the
facet labeller (GH #108); the second was the amount classifier, found by the
first live ``backfill-amounts`` run against the real archive.

The structural fix in both cases is ``client.messages.parse()`` with a schema,
which cannot return a fence at all. This helper is the belt-and-braces for the
paths that genuinely cannot use it — the subscription backend returns free
text — and the reason it lives here rather than beside either caller is that a
third caller writing its own ``json.loads`` is otherwise the obvious next step.
"""

from __future__ import annotations

import re

# Matches a fenced code block wrapping the whole payload, with or without a
# ``json`` language tag: ```json\n{...}\n``` or ```\n{...}\n```.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def strip_json_envelope(payload: str) -> str:
    """Best-effort unwrap of a markdown fence and/or surrounding prose.

    Handles a ```` ```json ```` fence, a bare ``` fence, and leading or
    trailing prose around the outermost JSON object. Never raises and never
    guarantees valid JSON — the caller's ``json.loads`` remains the actual
    validity check; this only improves the odds it succeeds.
    """
    text = payload.strip()
    fence_match = _FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text
