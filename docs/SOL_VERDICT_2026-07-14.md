# Sol's Verdict on the Build (2026-07-14)

Sol (GPT-5.6-sol, high effort, ~28 min) reviewed the whole system. It did not just read
the summary — **it explored the repo and executed attacks against the gates.** Where it
says "I proved that…", those are exploits it actually ran.

**This document is the punch list. Read it before writing any code.**

---

## 🔴 URGENT — do this first

**The OpenClaw gateway's master access key was found in a readable backup settings file**
and surfaced during Sol's inspection. **Rotate it.**

Also still outstanding: rotate the **xAI** and **OpenAI** keys (both were pasted into a
chat transcript).

---

## The verdict, verbatim

> "No. The system as it exists today will not reliably dispatch work, will not sustain
> the conversation you are imagining, and does not physically enforce its main safety
> promises."

> "It is currently a convincing dashboard and a collection of promising parts, with
> several labels claiming guarantees that the machine does not provide."

> "The overall idea is viable. The current implementation is not."

---

## THE BIGGEST ONE: nothing actually runs

> **"Chief says 'Putting Riggs on it,' and no builder starts."**

- The **text app returns "Got it" and starts nothing.**
- The **voice Chief has no tool that dispatches** — it can only promise.
- The **reviewer panel returns a list of names** and never launches anyone.
- The **streaming Chief (measured at 1.4s) is not connected to the voice** — the voice
  path still spawns a fresh process per utterance and waits for the whole answer. That
  is the exact lag the streaming session was built to fix.
- The standalone dispatcher is broken anyway: its config can't be read, it references a
  reviewer that doesn't exist, and it passes OpenClaw an unsupported background flag.

**The demo was real. The machine behind it is hollow.**

---

## Gates Sol BROKE (executed, not theorised)

| Claim | Reality |
|---|---|
| "Reviews are append-only" | **A failing review can be DELETED.** Editing fail→pass was blocked; deleting it was not. |
| "No screenshot, no verdict" | **A path to a file that does not exist is accepted**, and a passing tester verdict on top of it. |
| "Approvals need a recovery plan" | **An approval can be created already-granted**, skipping the check. |
| "Reviewers see one frozen version" | **Reviews aren't tied to a version.** Approve version A, builder changes it to B, **the old approval still counts.** Sol: the most dangerous flaw, because it produces *"believable green checks on code nobody reviewed."* |
| "At least two model families review" | Recorded, **not enforced.** |
| "Spend caps are hard" | The DB can refuse to *record* a charge. It cannot stop the provider **charging money before the record is written.** Voice and builder usage isn't being recorded at all. Only the provider's own cap is a real financial boundary. |
| "The top model is earned" | Tiering rules exist; **dispatch never uses them.** |
| "Local work can't ship unreviewed" | Only the *label* is protected. The local model can still write code into the real project, open a PR, or alter build automation. |
| "Agents physically cannot touch production" | **False.** No sandbox. No command restrictions. All builders share one real project folder, running with the owner's full privileges. |

---

## Two things wrong with the voice specifically

1. **The mouth's tool use is OPTIONAL.** `tool_choice: "auto"` means the model *may
   answer on its own instead of calling Chief.* The entire "everything goes to Chief"
   rule is **unenforced**. It must be forced.
2. **"Verbatim forwarding" is impossible.** The mouth hears audio and *generates* a text
   argument — that is another model interpreting the owner, **not a transcript**. Chief
   never sees his actual words.

---

## The conversation, honestly

- **Calm, one-at-a-time discussion: achievable.**
- **Fast overlapping back-and-forth: not yet.**
- **Approving anything dangerous by voice while driving: NO. Do not allow it.**
  For dangerous actions, Chief explains aloud — but approval waits until he has stopped
  and taps a written confirmation.

Specific failures to design against:
- The conservative speech detector may wait up to **8 seconds** before deciding he's
  finished. He pauses to merge; the conversation feels dead.
- Highway noise reads as continued speech; or cuts him off and sends half a command.
- A transcript turns **"don't deploy" into "deploy."**
- **"No, wait" stops the mouth's audio but does NOT stop Chief's pending work or a
  running builder.** He changes his mind; the old thing happens anyway.
- An old answer finishes *after* he's changed his mind and is spoken out of order.
- A reconnect sends the same request twice.

---

## The top five that will bite

1. **It says work started when nothing started.** *(This is the current state, not a
   prediction.)*
2. **"No, wait" fails to stop the old turn.**
3. **A builder damages something before any gate sees it** — they run with the owner's
   real authority today.
4. **The panel gives false confidence** — a pass can cover an older version, an
   unrelated screenshot, or evidence that never existed.
5. **The phone session dies in real driving conditions** — network changes, calls,
   navigation audio, screen lock, reconnects. No recovery.

---

## Missing entirely

- A **trusted action gatekeeper** holding all dangerous credentials. Agents ask it to
  act; it checks the approval, performs exactly that action once, rejects everything
  else. **This is the only thing that makes a database permission mean anything.**
- **Real sandboxes** — no production network, no secrets, no access outside one
  temporary work area. (OpenClaw supports this. It is **off by default**.)
- A **hard emergency stop** that doesn't depend on Chief being alive.
- **Separate cancellation** for: the speech, Chief's current thought, queued work, and a
  running builder.
- **Numbered conversation turns**, so an old answer can never arrive after a newer
  correction.
- **Duplicate protection** so a retry can't start the same job twice.
- **Reviews tied to the exact version tested.**
- **Crash recovery** — reconcile jobs after power loss, sleep, network failure, OpenClaw
  restart.
- **Tested backups** of job history and memory.
- **Real authentication on the phone app.** Being on the tailnet is not proof of being
  Neill.
- **Prompt-injection defence** — malicious instructions hidden in code, web pages,
  issues, dependencies, test output.
- **Supply-chain controls** — a builder can add a malicious package or alter the
  automation that holds repo secrets.
- **Memory hygiene** — wrong lessons need expiry, correction, provenance, strict project
  separation.
- **A realistic driving test set** — road noise, passengers, radio speech, weak signal,
  interruptions, self-corrections, ambiguous confirmations.

---

## Sol's actual recommendation

The concept works. The build does not. Before it operates unattended or touches anything
valuable, it needs:

1. **A real containment layer** — sandbox on, credentials absent, a trusted gatekeeper
   holding the dangerous powers, agents in disposable copies.
2. **A working dispatch path** — one guarded route, with all raw dispatch removed.
3. **A proper conversation controller** — turn numbering, cancellation, duplicate
   protection, reconnection.

> "Do not move forward on the belief that this is now a safe autonomous system."

---

## Caveat worth keeping

Sol's "I proved that…" claims (deletable fail verdicts, fake screenshot path accepted,
pre-granted approval) are **specific and checkable** — the exploit attempts are in its
transcript. Verify them against the schema before treating them as fact. They are
almost certainly right, but this project's whole lesson is not to take a model's word
for it.
