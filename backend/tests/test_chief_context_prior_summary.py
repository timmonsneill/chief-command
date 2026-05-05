"""Tests for the Phase 3 ``prior_summary`` block in chief_context.

The conversation_so_far block is conditional — it only appears when a
non-empty summary string is passed. When present, it sits between the
agent roster and the per-project memory inside Block 3 (the projects
block) and carries the auto-summarized provenance fence.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.chief_context import (  # noqa: E402
    DEFAULT_SCOPE,
    build_chief_system,
    build_chief_system_string,
)


def _all_text(blocks: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in blocks)


def test_no_summary_omits_conversation_so_far_block():
    """prior_summary=None → no conversation_so_far block in any block's text."""
    blocks = build_chief_system(DEFAULT_SCOPE)
    text = _all_text(blocks)
    assert "<conversation_so_far" not in text
    assert "# Conversation So Far" not in text


def test_blank_summary_omits_conversation_so_far_block():
    """prior_summary='   ' → still treated as no summary."""
    blocks = build_chief_system(DEFAULT_SCOPE, prior_summary="   \n  ")
    text = _all_text(blocks)
    assert "<conversation_so_far" not in text


def test_summary_present_injects_block():
    summary = "Decisions made: ship Phase 3.\nCurrent focus: memory layer."
    blocks = build_chief_system(DEFAULT_SCOPE, prior_summary=summary)
    text = _all_text(blocks)
    assert "<conversation_so_far" in text
    assert 'note="auto-summarized; may be lossy"' in text
    assert "ship Phase 3" in text
    assert "</conversation_so_far>" in text


def test_summary_block_position_between_roster_and_project_memory():
    """The conversation_so_far block must come AFTER the agent roster and
    BEFORE the per-project memory block — same authority level as roster."""
    summary = "PHASE3-SUMMARY-MARKER"
    blocks = build_chief_system(DEFAULT_SCOPE, prior_summary=summary)
    text = _all_text(blocks)

    # The roster header lives in the projects-block; per-project memory
    # follows. Positionally we just need: roster < summary < project memory.
    roster_idx = text.find("# Agent Roster")
    summary_idx = text.find("<conversation_so_far")
    project_idx = text.find("# Project Memory —")

    # Roster should always be present (agent files exist in the repo).
    if roster_idx == -1:
        # Roster missing in this dev environment — can't assert ordering vs
        # roster, but the summary should still be ahead of project memory.
        assert summary_idx != -1
        if project_idx != -1:
            assert summary_idx < project_idx
        return

    assert roster_idx < summary_idx, "summary must follow agent roster"
    if project_idx != -1:
        assert summary_idx < project_idx, "summary must precede project memory"


def test_string_flatten_propagates_summary():
    """build_chief_system_string is the Gemini path — must thread the summary
    through to the flattened output."""
    summary = "STRING-FLATTEN-MARKER"
    flat = build_chief_system_string(DEFAULT_SCOPE, prior_summary=summary)
    assert "<conversation_so_far" in flat
    assert "STRING-FLATTEN-MARKER" in flat


def test_string_flatten_no_summary_excludes_block():
    flat = build_chief_system_string(DEFAULT_SCOPE)
    assert "<conversation_so_far" not in flat
    assert "# Conversation So Far" not in flat


def test_summary_is_provenance_fenced_not_raw():
    """The summary body must land INSIDE the <conversation_so_far> tag, not
    leak out of the fence."""
    summary = "raw text body"
    blocks = build_chief_system(DEFAULT_SCOPE, prior_summary=summary)
    text = _all_text(blocks)
    open_idx = text.find("<conversation_so_far")
    close_idx = text.find("</conversation_so_far>")
    body_idx = text.find("raw text body")
    assert open_idx != -1
    assert close_idx != -1
    assert body_idx != -1
    assert open_idx < body_idx < close_idx
