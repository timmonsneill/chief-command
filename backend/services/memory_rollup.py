"""Rolling-summary memory for Chief's voice brain (Phase 3).

When a project accumulates enough raw turns since the last rollup, this
module fires a Flash-based summarizer that compresses (prior_summary,
new_turns) into a fresh summary, then writes it to ``voice_summaries``.
The summary is then injected into ``chief_context`` as a
``<conversation_so_far>`` fence so Chief picks up cross-session.

Flash, NOT Pro: rollup quality is fine on Flash and the per-turn cost
matters when this fires every ~30 turns. The brain itself stays Pro.

Failure model:
  * Every public function catches its own exceptions. Memory rollup is a
    best-effort enhancement — never a hard dependency for a turn. If the
    rollup writer 5xxs, we log and move on; the next turn falls back to
    raw recent history without summary.
  * ``maybe_rollup`` is fire-and-forget from the WS layer (called via
    ``asyncio.create_task``). It MUST NOT raise.

Concurrency:
  * A process-local asyncio.Lock keyed by project prevents two concurrent
    rollups for the same project from racing — would otherwise produce
    two summaries with overlapping watermarks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from services import history_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------
# Trigger a rollup when this many turns have accumulated since the last
# summary. 30 is roughly 5-10 minutes of voice conversation; small enough
# to keep summaries fresh, large enough that we don't fire Flash on every
# single turn.
ROLLUP_TRIGGER_TURNS: int = 30

# Hard cap on turns rolled in one Flash call. Protects against a runaway
# gap (e.g. a project that hasn't been summarized in days) from dumping
# thousands of turns into one prompt.
MAX_ROLLUP_TURNS: int = 80

# Output ceiling for Flash. Summaries are meant to be terse; capping this
# also caps the cost contribution of one rollup.
MAX_SUMMARY_TOKENS: int = 600

# Model id used for rollups. Stays separate from the brain model so a
# future model swap doesn't accidentally upgrade rollups to Pro.
ROLLUP_MODEL: str = "gemini-2.5-flash"


# Process-local concurrency guard. Multiple WS connections + a
# fire-and-forget pattern means we could otherwise schedule two rollups
# for the same project from two different turns. Lock keyed by project so
# different scopes still run independently.
_project_locks: dict[str, asyncio.Lock] = {}


def _lock_for(project: str) -> asyncio.Lock:
    lock = _project_locks.get(project)
    if lock is None:
        lock = asyncio.Lock()
        _project_locks[project] = lock
    return lock


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------
# Structured so Flash doesn't ramble. Each section is a fixed slot the
# model fills (or writes "(none)" if empty). Keeps summaries comparable
# across rollups and easy to glance at in logs.
_SUMMARIZER_SYSTEM_INSTRUCTION = (
    "You are summarizing a voice conversation between Neill and Chief, his AI "
    "orchestrator. Output a compact summary in this exact structure:\n"
    "\n"
    "**Decisions made:** (bullet list — concrete decisions Neill stated)\n"
    "**Current focus:** (one paragraph — what Neill is working on / cares "
    "about right now)\n"
    "**Open threads:** (bullet list — questions or work that's pending)\n"
    "\n"
    "Be terse. <=500 tokens total. Do NOT include opinions, embellishments, "
    "or filler.\n"
    "If the conversation has no clear decisions/focus/threads, write "
    "\"(none)\" for that section.\n"
    "If a prior summary is provided, treat it as starting context — do not "
    "lose facts from it; refold them into the new summary."
)


def _format_turns_for_prompt(turns: list[dict]) -> str:
    """Render role/content rows as a compact transcript for the summarizer.

    Format is ``user: ...`` / ``assistant: ...`` per line. Empty content
    rows are skipped — they'd just confuse the model.
    """
    lines: list[str] = []
    for entry in turns:
        role = entry.get("role") or ""
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        # Normalize role label for the prompt.
        label = "user" if role == "user" else "assistant" if role == "assistant" else role
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _build_user_prompt(prior_summary: Optional[str], turns_text: str) -> str:
    """Assemble the user-role payload Flash sees.

    Includes the prior summary (if any) as starting context, then the new
    transcript. Both are fenced so the model treats them as data — same
    pattern chief_context uses for memory files.
    """
    parts: list[str] = []
    if prior_summary:
        parts.append(
            "<prior_summary>\n" + prior_summary.strip() + "\n</prior_summary>"
        )
    parts.append("<new_turns>\n" + turns_text + "\n</new_turns>")
    parts.append(
        "Produce the updated summary now, following the section structure."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Flash invocation
# ---------------------------------------------------------------------------
async def _call_flash(prior_summary: Optional[str], turns: list[dict]) -> Optional[str]:
    """Call Gemini Flash to produce a fresh summary string.

    Returns the summary text on success, ``None`` on any error. The caller
    treats ``None`` as "skip this rollup, try again next time."

    Reuses ``gemini_brain._get_client`` so auth (api_key vs Vertex SA) is
    consistent with the brain path. We deliberately do NOT wire tools or
    streaming — this is a single non-interactive completion.
    """
    turns_text = _format_turns_for_prompt(turns)
    if not turns_text:
        return None

    user_prompt = _build_user_prompt(prior_summary, turns_text)

    try:
        # Lazy import — keeps services.memory_rollup importable in unit
        # tests that don't have google-genai installed (or want to mock
        # the call surface entirely).
        from google.genai import types

        from services.gemini_brain import _get_client

        client = _get_client()

        config = types.GenerateContentConfig(
            system_instruction=_SUMMARIZER_SYSTEM_INSTRUCTION,
            max_output_tokens=MAX_SUMMARY_TOKENS,
        )

        response = await client.aio.models.generate_content(
            model=ROLLUP_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=user_prompt)],
                ),
            ],
            config=config,
        )
    except Exception as exc:  # pragma: no cover — wide net is intentional
        # Provider errors, auth misses, transient 5xx, anything — log and
        # bail. Caller treats None as "no rollup written this round."
        logger.warning("memory_rollup: Flash call failed: %s", exc)
        return None

    # Extract text from response candidates. Mirrors the structure
    # gemini_brain.stream walks; here we just want the concatenated text.
    text_chunks: list[str] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or [] if content else []
            for part in parts:
                if getattr(part, "thought", False) is True:
                    continue
                piece = getattr(part, "text", None)
                if piece:
                    text_chunks.append(piece)
    except Exception as exc:
        logger.warning("memory_rollup: failed to parse Flash response: %s", exc)
        return None

    summary = "".join(text_chunks).strip()
    if not summary:
        logger.info("memory_rollup: Flash returned empty summary, skipping write")
        return None
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def maybe_rollup(project: str) -> None:
    """Fire-and-forget: rollup if enough new turns have accumulated.

    Flow:
      1. Count turns since the last summary for ``project``.
      2. If under ``ROLLUP_TRIGGER_TURNS``, no-op.
      3. Otherwise: pull up to ``MAX_ROLLUP_TURNS`` rows + the prior summary,
         call Flash, write the new summary to ``voice_summaries`` with
         ``covers_through_turn_id`` set to the highest id in the rolled
         window (so the next ``turns_since_summary`` call starts from
         there).
      4. Concurrent calls for the same project serialize on a
         process-local asyncio.Lock keyed by project.

    Never raises. Errors are logged and swallowed — this runs alongside
    the WS turn task and must not be able to break it.
    """
    if not project:
        return
    try:
        lock = _lock_for(project)
        # Non-blocking: if a rollup is already running for this project,
        # skip this one. The next turn will check again. Avoids stacking
        # rollup tasks under heavy use.
        if lock.locked():
            logger.debug(
                "memory_rollup: rollup already in flight for project=%s — skipping",
                project,
            )
            return
        async with lock:
            await _do_rollup(project)
    except Exception as exc:  # pragma: no cover — wide net intentional
        logger.warning("memory_rollup: maybe_rollup failed project=%s: %s", project, exc)


async def _do_rollup(project: str) -> None:
    """Actual rollup body. Caller holds the per-project lock."""
    n_new = await history_store.turns_since_summary(project)
    if n_new < ROLLUP_TRIGGER_TURNS:
        logger.debug(
            "memory_rollup: project=%s only %d new turns (<%d) — no rollup",
            project, n_new, ROLLUP_TRIGGER_TURNS,
        )
        return

    prior = await history_store.latest_summary(project)
    prior_summary_text = prior["summary_text"] if prior else None
    prior_watermark = int(prior["covers_through_turn_id"]) if prior else 0

    turns = await history_store.turns_to_rollup(
        project, since_turn_id=prior_watermark, limit=MAX_ROLLUP_TURNS,
    )
    if not turns:
        logger.debug(
            "memory_rollup: project=%s turns_since_summary=%d but turns_to_rollup empty",
            project, n_new,
        )
        return

    # Watermark policy: when we hit MAX_ROLLUP_TURNS, the rolled window
    # is a prefix of the new range. Set the watermark to the highest id
    # we actually summarized so the NEXT rollup picks up where this one
    # left off, not where the latest turn is. Otherwise we'd silently
    # drop the un-summarized tail.
    new_watermark = max(int(t["id"]) for t in turns)

    summary_text = await _call_flash(prior_summary_text, turns)
    if not summary_text:
        # _call_flash logged the why; nothing to write.
        return

    try:
        await history_store.append_summary(
            project=project,
            session_id=None,  # rollup spans sessions by design
            summary_text=summary_text,
            covers_through_turn_id=new_watermark,
            model=ROLLUP_MODEL,
        )
    except Exception as exc:
        logger.warning(
            "memory_rollup: append_summary failed project=%s: %s", project, exc,
        )
        return

    logger.info(
        "memory_rollup: wrote summary project=%s rolled_turns=%d watermark=%d "
        "summary_chars=%d",
        project, len(turns), new_watermark, len(summary_text),
    )


async def load_persistent_memory(
    project: str,
    raw_limit: int = 20,
) -> tuple[Optional[str], list[dict]]:
    """Return ``(summary_text, recent_turns)`` for a project.

    ``summary_text`` is the latest rolling summary or ``None`` if no
    summary has ever been written. ``recent_turns`` is the existing
    ``load_recent_for_project`` shape — list of ``{role, content}``
    dicts, oldest-first, capped at ``raw_limit``, ACROSS sessions.

    Caller injects the summary into the system prompt and passes
    ``recent_turns`` as the brain's history kwarg.

    Best-effort: any failure returns ``(None, [])`` and logs. The brain
    can still answer from its system prompt + tools; we just lose the
    cross-session memory edge for this turn.
    """
    summary_text: Optional[str] = None
    recent_turns: list[dict] = []
    try:
        prior = await history_store.latest_summary(project)
        if prior:
            summary_text = prior.get("summary_text") or None
    except Exception as exc:
        logger.warning("memory_rollup: latest_summary load failed: %s", exc)

    try:
        recent_turns = await history_store.load_recent_for_project(
            project, limit=raw_limit,
        )
    except Exception as exc:
        logger.warning("memory_rollup: load_recent_for_project failed: %s", exc)
        recent_turns = []

    return summary_text, recent_turns


__all__ = [
    "ROLLUP_TRIGGER_TURNS",
    "ROLLUP_MODEL",
    "MAX_ROLLUP_TURNS",
    "MAX_SUMMARY_TOKENS",
    "maybe_rollup",
    "load_persistent_memory",
]
