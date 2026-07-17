# Sol's Verdict — Round 8 (2026-07-17)

Full transcript: `docs/sol/sol_round5.out`. **Verdict: not yet — but four narrow items
from sign-off**, and Sol committed: *"Once those are added, I would regard the remaining
work … as build-phase proof rather than further architectural debate."*

Scorecard on its round-7 conditions: claims/benchmark corrections **pass**; Decision E
**honestly recorded**; connected-tool exclusions **mostly pass**; admission rules
**partly** — governance of the admission process itself was missing.

## The four remaining items

1. **Owner-controlled, continuously valid admission (design).** Only the authenticated
   owner may create/change/revoke admissions, the allowlist, connected-tool approvals,
   or the EMR refusal — stored outside anything an agent can edit. Admission is not
   permanent: the broker re-checks the live repository state against the recorded proof
   at each action, and a proposed change that would ADD deploy/publish/privileged
   automation behavior cancels the repo's admission before it can be pushed or merged.
   Repos identified by GitHub's permanent ID, not renameable names. The "owner's tap =
   manual deploy step" exception only counts where the tap physically starts the
   deployment — it cannot rescue a repo where merge itself deploys.
2. **Gauntlet before the first push (design).** Sol caught a sequencing conflict: the
   admission rule said no automation runs agent code before review, but the broker text
   contemplated pushing branches pre-review. Resolution: the exact reviewed version
   passes the gauntlet BEFORE its first branch push; no repository automation ever runs
   unreviewed agent code.
3. **A genuinely narrower GitHub action path (design).** GitHub's own permission model
   bundles contents-write (needed to push/merge) with release creation — so "the broker's
   token can't create releases" is factually unachievable with token scoping alone
   (Sol cited GitHub's docs). The broker needs an independently enforced outer operation
   filter that permits only the exact push/PR/merge operations and survives the broker
   itself being fooled or compromised; the build must attack it.
4. **Two overstatements (wording).** "Real money" is not behind a door — routine spend
   is allowed; say "spending beyond the fixed limits." And the autonomy risk doesn't
   land "almost entirely" on merges and spend — continuous audio exposure, queue
   flooding, and provider disclosure are also new versus the terminal.

## Confirmed honest by Sol's code spot-check

The doc's no-credit-claimed stance matches reality: admission system and GitHub broker
not built; live record still allows verdict deletion; the one "shipped" job lacks run
identity; provider credential and direct execution paths still present — all correctly
presented as unfinished build/decommissioning work.
