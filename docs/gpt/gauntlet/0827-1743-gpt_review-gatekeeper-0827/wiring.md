## Findings

**CRITICAL — The gatekeeper service is bypassed for paid reviews.**  
[harness/gauntlet.py:321](/Users/user/code-projects/chief-command/harness/gauntlet.py:321), [harness/gatekeeper.py:553](/Users/user/code-projects/chief-command/harness/gatekeeper.py:553)

The panel imports `gatekeeper.spend()` directly and gives it the panel’s writable database connection. It never contacts the authenticated loopback service.

Concrete scenario: the gatekeeper process is stopped or its token is unavailable → a metered review starts → the panel writes the reservation directly and calls the paid provider anyway. The promised “gatekeeper down means irreversible actions stop” boundary therefore does not exist on the live path.

**HIGH — Successfully reviewed work has no route to shipping.**  
[harness/gauntlet.py:522](/Users/user/code-projects/chief-command/harness/gauntlet.py:522), [harness/gatekeeper.py:173](/Users/user/code-projects/chief-command/harness/gatekeeper.py:173), [harness/db/schema.sql:997](/Users/user/code-projects/chief-command/harness/db/schema.sql:997)

The panel stops after changing a job to `done`; nothing calls `merge()`. Production code also never records a tester verdict, which shipping structurally requires.

Concrete scenario: a worker builds successfully and every configured reviewer passes → job becomes `done` → no tester runs and no merge request is sent → the isolated branch never reaches main. Even if a tester verdict is added externally, nothing invokes the merge gate. The dashboard says “Checked, ready,” but the work cannot ship.

**HIGH — Deploy consumes the owner’s one-time approval without deploying anything.**  
[harness/gatekeeper.py:420](/Users/user/code-projects/chief-command/harness/gatekeeper.py:420), [harness/gatekeeper.py:426](/Users/user/code-projects/chief-command/harness/gatekeeper.py:426)

Concrete scenario: a valid owner approval exists → `deploy()` marks it used → no deployment mechanism runs → the function returns a granted receipt. Nothing was deployed, and retrying requires a new approval because the original has already been consumed.

**HIGH — Actual Grok usage never reaches the budget record or dashboard.**  
[harness/gauntlet.py:178](/Users/user/code-projects/chief-command/harness/gauntlet.py:178), [harness/gauntlet.py:188](/Users/user/code-projects/chief-command/harness/gauntlet.py:188), [harness/gauntlet.py:370](/Users/user/code-projects/chief-command/harness/gauntlet.py:370)

The provider response’s usage data is discarded; every review reserves a fixed five cents.

Concrete scenario: a large review costs twelve cents → the database and dashboard record five cents → repeated reviews can exceed the advertised hard daily ceiling while the record still reports room remaining.

**HIGH — The branch adds an executable migration despite the binding rule requiring a proposal.**  
[AGENTS.md:94](/Users/user/code-projects/chief-command/AGENTS.md:94), [007_no_job_completes_unreviewed_2026-07-21.sql:1](/Users/user/code-projects/chief-command/harness/db/migrations/007_no_job_completes_unreviewed_2026-07-21.sql:1)

Concrete scenario: this branch is accepted and its numbered migrations are applied during deployment → an agent-authored live database change is treated as approved rather than held as `PROPOSED_MIGRATION.sql` for owner review. This is direct drift from a non-negotiable repository rule.

I am not separately reporting “the migration is never automatically applied”: deliberate manual application is established repository policy, and the handoff says migrations 001–007 were applied. The finding is the forbidden committed migration itself.

**MEDIUM — Gatekeeper-only records are not exposed on the dashboard.**  
[harness/gatekeeper.py:113](/Users/user/code-projects/chief-command/harness/gatekeeper.py:113), [harness/server.py:152](/Users/user/code-projects/chief-command/harness/server.py:152)

Concrete scenario: a jobless deploy request is refused → the refusal exists only in `gate_log` → `/api/state` returns seats, jobs, and projects but never that log → the owner cannot see the refusal later. The record-to-dashboard leg is missing.

**MEDIUM — Paid dispatch silently discards excluded-reviewer information.**  
[harness/dispatch.py:330](/Users/user/code-projects/chief-command/harness/dispatch.py:330), [harness/dispatch.py:340](/Users/user/code-projects/chief-command/harness/dispatch.py:340)

`excluded` is computed but never recorded on the paid dispatch path, unlike local dispatch.

Concrete scenario: Grok lacks its key while two other families remain available → dispatch succeeds with the smaller panel → no skipped event is stored → the dashboard makes the reduced panel look intentional and complete.

## Tests

The requested command was run, but the read-only environment provided no writable temporary directory, so pytest stopped before collection: **0 tests executed**. A read-only collection run found **202 tests**. This review therefore cannot claim a green test run.

**VERDICT: NO-GO**
