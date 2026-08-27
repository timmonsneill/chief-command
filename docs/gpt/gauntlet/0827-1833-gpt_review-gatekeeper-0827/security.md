## Findings

1. **CRITICAL — The gatekeeper is not a security boundary.**  
   `harness/gatekeeper.py:41`

   Scenario: a prompt-injected agent runs as the owner’s operating-system account → it directly runs a merge command, edits the safety database, reads the gatekeeper token, or calls a paid provider using inherited credentials → code merges or money is spent without consulting the gatekeeper. The source acknowledges this limitation, but it leaves all three protected powers bypassable.

2. **CRITICAL — Normal paid-work paths bypass the authenticated service.**  
   `harness/dispatch.py:367`, `harness/gauntlet.py:321`

   Scenario: the gatekeeper service is stopped or its token is unavailable → dispatch or the review panel imports the internal spending function directly → the reservation succeeds and the paid provider is called anyway. Thus the stated “gatekeeper unavailable means irreversible actions stop” rule is false even for normal operation.

3. **HIGH — CLI reviewers inherit credentials and can inspect more than their frozen bundle.**  
   `harness/gauntlet.py:113`, `harness/gauntlet.py:136`, `harness/gauntlet.py:395`, `harness/gauntlet.py:423`

   Scenario: reviewed work contains a prompt injection telling the reviewer to inspect its environment or checkout and answer `PASS <secret>` → the Claude/Codex subprocess inherits the server environment and working directory → the reviewer reads an API key or unrelated file → `_parse_verdict` accepts that line and `record_verdict` stores the secret in the database and dashboard-visible summary. No sanitized environment, isolated directory, or tool restriction enforces the “bundle only” promise.

4. **HIGH — A concurrent late failure can arrive after the gatekeeper merges.**  
   `harness/gatekeeper.py:198`, `harness/gatekeeper.py:298`, `harness/gauntlet.py:414`, `harness/gauntlet.py:433`, `harness/gauntlet.py:529`

   Scenario: one panel certifies a job while a duplicate panel still has a reviewer running → merge checks the existing verdicts and starts its write transaction → the second reviewer’s failure waits for that transaction → merge commits and marks the job shipped → the failure then lands while the second panel’s `decided` flag is still false. That panel returns on its failure without undoing the shipped status, leaving rejected code merged.

5. **HIGH — Recorded estimates do not enforce the real spending caps.**  
   `harness/dispatch.py:25`, `harness/gauntlet.py:59`, `harness/gauntlet.py:168`, `harness/gauntlet.py:388`

   Scenario: a large paid review costs more than the fixed five-cent reservation → the provider’s usage information is discarded → only five cents reaches the ledger → repeated calls exceed the advertised daily limit while the record still reports money available. Metered builds have the same fixed-estimate problem.

I did not find shell injection: changed command execution uses argument arrays and merge branch names are narrowly validated. SQL is parameterized. I also did not report a public listener because the current entrypoint uses loopback and no changed caller supplies a public address, although `serve(host=...)` remains an avoidable footgun.

Tests: the exact required command was run, but the read-only environment had no writable temporary directory, so **0 tests executed**. A read-only collection pass found **209 tests**; a green run was not independently verified.

**VERDICT: NO-GO**
