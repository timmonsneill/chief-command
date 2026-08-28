# Direction of record: one HOME, many WORKERS — 2026-08-28 (owner-set)

**Owner's words:** "one small agent on my laptop, bigger agent(s) on my Mac Studio when
it comes in about a month, and honestly a backup if I can't reach the machine … if my
office loses wifi I lose the whole shebang. I'd like to avoid that." And: a worker being
unreachable "shouldn't really matter."

## The shape
- **HOME** — always-on, small, reachable: holds the RECORD (`chief.db`), the GATEKEEPER
  (the only door to the record), the APP the phone opens, and the VOICE. A small cloud
  Linux box (~$20–40/mo). No GPU. If the home is up, Neill can always talk to Chief,
  hand out jobs, see status, read results.
- **WORKERS** — wherever the muscle is, connected over the tailnet, PULLING jobs they are
  suited for and reporting back. They never own the record.
  - laptop: the small agent (local free model, light jobs)
  - Mac Studio (arriving ~late Sept 2026): the big agents, heavy builds, local models
  - cloud worker (the home box or a second small one): the BACKUP — Claude/Codex signed
    in there, or API seats. Takes jobs when the Studio is unreachable.
- A worker that is off is just off. Jobs it had not started wait or go to another
  worker; jobs it was mid-way through are marked STALLED on the record, visibly. When
  it returns it picks up. Nothing is lost; there is still exactly ONE record.

## Why this is not "two truths"
Workers ask the record for work (`claim_next_job` is already atomic) and report back;
the gatekeeper is the only writer of merge/deploy/spend. Moving workers to other machines
is plumbing on a design that already assumes it.

## What changes, in order (do NOT migrate twice)
1. This week: test on the laptop as-is.
2. Before the Studio arrives: stand up the HOME on a cloud box — record, gatekeeper (as a
   service, queue 7), app, voice, THE WALL (queue 13 — built for Linux, once). Sol design
   pass first (queue 14).
3. Point the Studio at the home as a worker on day one; the laptop becomes a worker too.
4. Cloud worker = the backup seat set, capped by the record like everything else.

## Constants
- Tailnet-only; nothing public (rule 3). Arch stays off-limits to all workers (Decision C).
- Providers by seat; the home is hardware-agnostic (rule 4) — the cloud box is "a Linux
  host with Tailscale", nothing more specific.
- Manual fallback never goes away: Neill can open Claude Code / Codex by hand anywhere.
