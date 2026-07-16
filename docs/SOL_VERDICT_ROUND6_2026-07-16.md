# Sol's Verdict — Round 6 (2026-07-16)

Sol attacked the rewritten `ARCHITECTURE_v2_2026-07-14.md` (fail-closed reviewer seat,
authority-based classification, separated powers, exclusive signed release path). Full
transcript: `docs/sol/sol_round3.out`. **Verdict: NOT approved.**

Closing line: *"The rewritten document corrects the round-5 conclusions, but it does not
yet turn them into an architecture that survives the attacks it now acknowledges."*

---

## The trajectory — the finding about the process itself

Round 5 demanded 12 changes and 14 missing designs. Round 6 accepted that those were
folded in — then demanded **13 more changes and 16 more missing designs, deeper than the
last set**: signed release *authorizations* covering environment/commands/secrets/
schedules (not just package signatures), independent build reproduction, signing-key
governance with hardware protection and trust-root rotation, multi-person administrator
custody, compromise tolerance for every trusted component, monitoring that works while a
hostile workload resists it, availability protection for the queue and kill switch.

**The loop is not converging. Each round's fixes legitimize a deeper layer of demands.**
Sol is now specifying the supply-chain security architecture of a production enterprise —
each finding individually valid, and collectively far beyond what a one-person harness on
a personal Mac can build or operate. The root cause: **the design gives the machine
release authority, so Sol correctly requires everything a release authority needs.**

## The two findings that are project-shaping regardless

1. **The bootstrap contradiction.** With the reviewer seat empty and the system failing
   closed, the classifier, signer, record-writer, verifier, and containment controls are
   themselves high-authority — so **the first trustworthy installation of this system
   cannot be approved. The system cannot become trusted or control production until a
   qualified human has inspected its initial implementation.** Fail-closed + empty seat
   doesn't defer the reviewer; it makes one a precondition of the whole release
   architecture existing.
2. **A single compromised trusted component still wins.** Sol's table: a compromised
   merge caller merges anything; a compromised signer creates what production treats as
   valid; a compromised deployer runs signed code under unreviewed settings; a
   compromised record-writer appends lies (append-only stops erasure, not lying); a
   compromised web UI can show Neill one action while getting his signature on another.
   Separation of names is not separation of power until each component independently
   verifies signed prerequisites.

## Also of note

- The authority-description bypass: an agent-written "this component can't do much"
  claim must itself be high-authority and independently verified, or it's the new hole.
- The queue design, reviewer governance, release state machine, key/withdrawal/admin
  governance, non-package production changes (flags, schedules, DB procedures), safe
  cutover from the legacy paths, and availability (queue flooding, kill-switch denial)
  are all missing designs.
- The full "must be proven during the build" list is preserved in the transcript.

## Status

Verdict recorded; NOT folded into another rewrite yet. The proportionality question —
whether this harness should hold release authority at all, versus producing only PRs
that a human merges — goes to Neill before any further iteration.
