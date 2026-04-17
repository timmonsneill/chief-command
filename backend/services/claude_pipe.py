"""Deprecated: subprocess-based Claude Code integration removed in v2.

Use backend.services.llm.stream_turn instead.
"""


class DeprecationError(Exception):
    pass


def __getattr__(name: str):
    raise DeprecationError(
        f"claude_pipe.{name} is removed. Use services.llm.stream_turn instead."
    )
