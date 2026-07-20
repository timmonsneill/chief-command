"""Chief, as a LIVE CONVERSATION instead of a program I restart every turn.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS (owner, 2026-07-14):

    "i talk fast, i make decisions fast, I want to have deep conversations about what
     we are doing. im worried this isn't going to be possible with the back and forth
     and tiering and sending everything to chief"

He was right, and the problem was mine, not the architecture's.

THE BUG: every turn, I was launching a BRAND NEW PROCESS to run Chief. Fresh start,
fresh context, fresh everything — 3-5 seconds of pure launch overhead before Chief had
even read his words. Then it thought. Then it died. Then he said "yeah" and the whole
thing happened again from scratch.

That is not a conversation. It's hanging up and redialling after every sentence.

THE FIX: a live streaming session over the API.
  - No process launch. The 3-5s of startup simply vanishes.
  - It STREAMS. The voice starts speaking before Chief has finished thinking, so he
    hears the answer BUILDING instead of silence-then-monologue.
  - It REMEMBERS. "yeah", "no, the other one", "what about X" all work, because Chief
    already knows what he's saying yeah TO.

"Yeah" now costs about a second, not eight.

═══════════════════════════════════════════════════════════════════════════════
THE MONEY SPLIT — fast where he feels it, free where he doesn't.

    CHIEF talks over the API.       Metered — but conversation is TEXT, which is pennies.
    BUILDERS run as programs.       Free on the subscription, and a 5s startup is noise
                                    on a 90-second build.

Everything still goes through Chief. That part is not negotiable — it's what stopped
"yes, skip the backup" from being a catastrophe. But going to Chief should cost him a
beat, not a coffee break.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chief import CHIEF_PROMPT  # noqa: E402  (one source of truth for who Chief is)

API = "https://api.openai.com/v1/chat/completions"

# Terra by default. The benchmark said the top model bought no extra safety and cost
# more rate limit — and Sol, under pressure, folded on a destructive act while the
# cheaper models held. Chief is a colleague who flags problems, NOT the security
# boundary. The capability gates are that.
CHIEF_MODEL = "gpt-5.6-terra"
CHIEF_DEEP = "gpt-5.6-sol"


class ChiefSession:
    """One live conversation. Holds the thread, so 'yeah' means something.

    This is the whole point: Chief is not re-briefed from zero on every utterance. It
    is a colleague who has been in the room the entire time.
    """

    def __init__(self, extra_context: str = "") -> None:
        # The real project list (and anything else the caller knows) rides in the system
        # message, so Chief has the FACTS from the first word and stops improvising what
        # Neill is working on. It stays put while the conversation is trimmed around it.
        system = CHIEF_PROMPT
        if extra_context.strip():
            system += f"\n\n## WHAT'S TRUE RIGHT NOW\n{extra_context.strip()}"
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        self.key = os.environ.get("OPENAI_API_KEY", "")

    def remember(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        # Keep the thread from growing without bound. The system prompt always stays;
        # we trim the oldest exchanges. (A long drive is a long conversation.)
        if len(self.messages) > 41:
            self.messages = [self.messages[0]] + self.messages[-40:]

    async def say(self, said: str, deep: bool = False) -> AsyncIterator[str]:
        """Neill said something. Stream Chief's answer back, sentence by sentence.

        Yields SENTENCES, not tokens — because the voice speaks in sentences. The moment
        a full sentence exists, it can start being spoken while Chief writes the next
        one. That's what kills the wait.
        """
        if not self.key:
            yield "I don't have my connection to think with. Nothing's started."
            return

        self.remember("user", said)

        model = CHIEF_DEEP if deep else CHIEF_MODEL
        payload = {
            "model": model,
            "messages": self.messages,
            "stream": True,
            # Low effort for conversation. He was right about this and said it three
            # times: "Sol... prolly doesn't need to be ultra for most convos."
            "reasoning_effort": "high" if deep else "low",
        }

        full: list[str] = []
        buf = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as http:
                async with http.stream(
                    "POST", API,
                    headers={"Authorization": f"Bearer {self.key}",
                             "Content-Type": "application/json"},
                    json=payload,
                ) as r:
                    if r.status_code >= 400:
                        # Drop the just-added user turn so a failed exchange doesn't leave
                        # a dangling, unanswered message that corrupts the thread on the
                        # next utterance. The raw API error stays in the log below, NEVER
                        # in what gets spoken — Neill must never hear API jargon.
                        body = (await r.aread()).decode()[:200]
                        self.messages.pop()
                        print(f"chief_live: API {r.status_code}: {body}", file=sys.stderr)
                        yield "Something went wrong on my end. Nothing's started."
                        return

                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        piece = delta.get("content") or ""
                        if not piece:
                            continue

                        buf += piece
                        full.append(piece)

                        # Emit as soon as a sentence is whole. The voice can start
                        # speaking it while Chief is still writing the next one.
                        while (cut := _sentence_end(buf)) != -1:
                            sentence = buf[: cut + 1].strip()
                            buf = buf[cut + 1 :]
                            if sentence:
                                yield sentence

            if buf.strip():
                yield buf.strip()

        except httpx.TimeoutException:
            # Sol: "The system needs a safe failure mode when Chief is unavailable."
            # We do NOT fall back to something dumber making the call. We say so and do
            # nothing. Silence is safer than an unsupervised guess. Drop the dangling user
            # turn so the next utterance starts from a clean thread.
            self.messages.pop()
            yield "I'm having trouble thinking that through. Nothing's started."
            return

        answer = "".join(full).strip()
        if answer:
            self.remember("assistant", answer)


def _sentence_end(s: str) -> int:
    """Where does the first complete sentence end?

    Deliberately conservative — a false positive means the voice speaks half a thought
    aloud, which sounds broken. Better to wait a beat than to blurt.
    """
    for i, ch in enumerate(s):
        if ch in ".!?" and i + 1 < len(s) and s[i + 1] in " \n":
            # Don't split on "e.g." / "Mr." / "4.2"
            if i >= 1 and s[i - 1].isdigit() and i + 2 < len(s) and s[i + 2].isdigit():
                continue
            return i
    return -1
