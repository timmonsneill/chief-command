"""LLM streaming integration for Chief Command — Phase 2 (Gemini-on-Vertex).

This module is a THIN adapter that maps the legacy ``stream_turn(...)`` call
signature onto the new ``services.gemini_brain.stream(...)`` flow. Keeping
the wrapper here means callers (``app.websockets._run_llm_turn``) don't need
to know which provider is wired in — they pass history + callbacks + scope
context the same way they always did.

What changed at the API surface vs the legacy Anthropic version:
  * ``model`` is accepted but ignored for routing — a single Gemini brain is
    wired in (see ``services.gemini_brain.GEMINI_MODEL``; currently
    ``gemini-2.5-pro``). The returned ``usage_dict["model"]`` reports that
    same id (the Phase 2 cost-tracker bucket).
  * ``system_blocks`` (Anthropic-shaped) is still accepted; we flatten the
    block list to a single string for Gemini's ``system_instruction`` field.
    Callers that pass ``project_scope`` instead get a fresh build via
    ``chief_context.build_chief_system_string``.
  * Three NEW kwargs: ``send_tool_call`` (optional WS hook for tool-chip
    frames), ``cwd`` (active scope's repo root — required when tools fire),
    ``subject`` (JWT subject — required for the dispatch_agent CC pool key).
    All three carry sensible defaults so existing tests / callsites that
    don't have them keep working.

Cancellation contract is preserved verbatim from the legacy llm.py — see
``gemini_brain.stream`` for the actual implementation. The two layers that
matter:
  1. ``asyncio.current_task().cancelling()`` is checked between SDK chunks.
     No extra awaits after a cancel decision.
  2. CancelledError propagates without trying to drain remaining stream
     bytes; the SDK's iterator handles its own teardown via __aexit__/GC.
  3. If a tool call is in flight on cancel, agent_tools.execute_dispatch_agent
     forwards the interrupt to the CC subprocess.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from services import gemini_brain
from services.chief_context import build_chief_system_string

logger = logging.getLogger(__name__)


UsageRecord = dict


def _flatten_system_blocks(system_blocks: Optional[list[dict]]) -> str:
    """Concat the Anthropic-shaped block list to a single string.

    Skips empty blocks. Returns empty string when system_blocks is None /
    empty — the caller is then expected to fall back to the project_scope
    builder path.
    """
    if not system_blocks:
        return ""
    return "\n\n".join(
        block.get("text", "")
        for block in system_blocks
        if isinstance(block, dict) and block.get("text")
    )


async def stream_turn(
    history: list[dict],
    model: str,
    send_token: Callable[[str], Awaitable[None]],
    send_tts_sentence: Callable[[str], Awaitable[None]],
    *,
    # 2026-05-05 latency cut: voice default drops 1024 -> 384. The voice
    # WS handler doesn't pass ``max_tokens``, so this default IS the
    # voice path's cap. Defers to ``gemini_brain.DEFAULT_MAX_OUTPUT_TOKENS``
    # so future tuning happens in one spot.
    max_tokens: int = gemini_brain.DEFAULT_MAX_OUTPUT_TOKENS,
    project_scope: Optional[str] = None,
    system_blocks: Optional[list[dict]] = None,
    send_tool_call: Optional[Callable[[dict], Awaitable[None]]] = None,
    on_tool_round_complete: Optional[Callable[[str], Awaitable[None]]] = None,
    cwd: Optional[Path] = None,
    subject: str = "owner",
    cancel_event: Optional[asyncio.Event] = None,
) -> UsageRecord:
    """Run one Gemini turn and stream tokens / TTS sentences / tool-call frames.

    Maintains the legacy signature so the WS handler keeps working without
    changes. ``model`` is accepted but ignored — the Gemini brain id is
    pinned in ``services.gemini_brain.GEMINI_MODEL`` (currently
    ``gemini-2.5-pro``). Returns a usage dict with the same keys legacy
    callers expect plus ``model`` set to that id.

    See module docstring for the full migration notes.

    history MUST end with the user's latest message (role=user). We pop
    that off and feed it to Gemini as the new turn so the conversation
    history continues correctly. If history is empty we emit a single
    placeholder turn — never crash, always reply.
    """
    if not history:
        logger.warning("stream_turn: history is empty; replying with empty turn")
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "model": gemini_brain.GEMINI_MODEL,
            "stop_reason": "no_history",
            "assistant_text": "",
            "cost_cents": 0,
        }

    # The WS handler appends the user turn before calling stream_turn. Pop
    # the trailing user entry so we don't double-include it in Gemini's
    # contents list (history -> Content[]) AND as the user_text param.
    if history[-1].get("role") != "user":
        logger.warning(
            "stream_turn: history doesn't end with user role (got %r); "
            "appending a synthetic user turn",
            history[-1].get("role"),
        )
        user_text = ""
        prior_history = history
    else:
        user_text = history[-1].get("content", "") or ""
        prior_history = history[:-1]

    # System instruction. Prefer the explicit blocks passed by the caller
    # (chief_context.build_chief_system) and flatten to one string. Fall
    # back to the scope-aware builder if blocks weren't provided. Last
    # resort: a generic Chief identity hint.
    system_prompt = _flatten_system_blocks(system_blocks)
    if not system_prompt and project_scope:
        try:
            system_prompt = build_chief_system_string(project_scope)
        except Exception as exc:
            logger.warning("stream_turn: chief_context build failed: %s", exc)
    if not system_prompt:
        system_prompt = (
            "You are Chief, a sharp personal AI assistant for a software "
            "owner. Concise, direct, one-to-two sentences in voice mode."
        )

    # cwd is required for in-process tool execution (Read/Bash/Grep) and
    # for the dispatch_agent CC pool key. If the WS layer didn't supply
    # one, look it up from the scope; on failure we fall through with cwd
    # = home (tools will deny anything attempting to escape, since the
    # path-fence machinery anchors on cwd).
    if cwd is None:
        cwd = _resolve_cwd(project_scope)
    if cwd is None:
        # Tools will reject every call against this fallback (path-fence
        # rejects everything outside cwd, which is now home). That's
        # exactly what we want — the model can still chat without tools.
        cwd = Path.home()
        logger.warning(
            "stream_turn: no repo cwd resolved for scope=%r — tools will be "
            "deny-only",
            project_scope,
        )

    scope_str = project_scope or ""

    # System prompt append for dispatch_agent — the CC subprocess gets the
    # SAME flattened identity so its replies stay on-brand.
    system_prompt_append = system_prompt

    return await gemini_brain.stream(
        history=prior_history,
        user_text=user_text,
        system_prompt=system_prompt,
        send_token=send_token,
        send_tts_sentence=send_tts_sentence,
        send_tool_call=send_tool_call,
        on_tool_round_complete=on_tool_round_complete,
        cwd=cwd,
        subject=subject,
        scope=scope_str,
        system_prompt_append=system_prompt_append,
        max_output_tokens=max_tokens,
        cancel_event=cancel_event,
    )


def _resolve_cwd(project_scope: Optional[str]) -> Optional[Path]:
    """Look up the active scope's repo path via repo_map.get_repo_path."""
    if not project_scope:
        return None
    try:
        from services.repo_map import get_repo_path
        return get_repo_path(project_scope)
    except Exception as exc:
        logger.warning("stream_turn: get_repo_path raised: %s", exc)
        return None


__all__ = ["stream_turn", "UsageRecord"]
