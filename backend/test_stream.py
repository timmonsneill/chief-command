"""Quick smoke test: call stream_turn once and print first-token latency + usage."""

import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from services.llm import stream_turn


async def main():
    first_token_time = None
    start = time.perf_counter()

    tokens = []

    async def on_token(text: str):
        nonlocal first_token_time
        if first_token_time is None:
            first_token_time = time.perf_counter() - start
            print(f"First token latency: {first_token_time*1000:.0f}ms", flush=True)
        tokens.append(text)

    async def on_sentence(sentence: str):
        pass

    usage = await stream_turn(
        history=[{"role": "user", "content": "hi"}],
        model="claude-haiku-4-5",
        send_token=on_token,
        send_tts_sentence=on_sentence,
    )

    total_elapsed = time.perf_counter() - start
    print(f"Total time: {total_elapsed*1000:.0f}ms")
    print(f"Response: {''.join(tokens)!r}")
    print(f"Usage: input={usage['input_tokens']} output={usage['output_tokens']} cached={usage['cache_read_input_tokens']} cost={usage['cost_cents']}¢")
    print(f"Model: {usage['model']}  stop_reason: {usage['stop_reason']}")


if __name__ == "__main__":
    asyncio.run(main())
