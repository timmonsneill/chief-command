NO-GO: three high-severity runtime bugs and one database-contract mismatch.

### Findings

1. **HIGH — A second/concurrent review panel can leave a failed job marked done**

   [harness/gauntlet.py:433](/Users/user/code-projects/chief-command/harness/gauntlet.py:433), [harness/gauntlet.py:529](/Users/user/code-projects/chief-command/harness/gauntlet.py:529)

   Scenario: panel A passes and marks the job done. Panel B, already running, records a failure before setting its own `decided` flag. The late-failure recovery does not run because that flag is still clear; panel B then sees its failure and returns early without moving the job back to review. Outcome: the database contains a current-version failure while the job remains `done`. I reproduced this state directly: `late_verdict=fail status=done fails=1`.

2. **HIGH — Merge blocks all database writers longer than their timeout**

   [harness/gatekeeper.py:298](/Users/user/code-projects/chief-command/harness/gatekeeper.py:298), [harness/gatekeeper.py:305](/Users/user/code-projects/chief-command/harness/gatekeeper.py:305), [harness/db/jobs.py:52](/Users/user/code-projects/chief-command/harness/db/jobs.py:52)

   Scenario: job A starts merging and holds an immediate write transaction while the Git operation may run for 120 seconds. A paid reviewer for job B finishes during that window and tries to record its verdict. The connection waits only 15 seconds, then fails; the review thread records the reviewer as skipped even though the provider was called and paid for. Outcome: job B parks without its completed verdict and the review spend is wasted.

3. **HIGH — A failed paid spawn leaves a charged, unstarted job permanently queued**

   [harness/dispatch.py:366](/Users/user/code-projects/chief-command/harness/dispatch.py:366), [harness/dispatch.py:375](/Users/user/code-projects/chief-command/harness/dispatch.py:375)

   Scenario: a metered builder successfully reserves 25 cents, then the worker command is missing, times out, or otherwise raises. There is no cleanup around the spawn. Outcome: the exception escapes, while the job remains `todo` with no run identifier or error and the 25-cent reservation remains. I reproduced exactly: `status='todo', run_id=None, error=None, usage=25`.

4. **MEDIUM — The gatekeeper rejects a state the new database guards explicitly accept**

   [harness/gatekeeper.py:224](/Users/user/code-projects/chief-command/harness/gatekeeper.py:224), [harness/db/schema.sql:877](/Users/user/code-projects/chief-command/harness/db/schema.sql:877), [harness/db/schema.sql:909](/Users/user/code-projects/chief-command/harness/db/schema.sql:909)

   Scenario: a legacy or directly created job has both stored requirements at zero, receives one valid current-version review from another family, and reaches `done`. The schema deliberately treats zero as a minimum floor of one, but merge treats zero as invalid and refuses permanently. I reproduced a `done` job accepted by both triggers and then refused by the gatekeeper for having no requirements.

I excluded weaker candidates: the service defaults to loopback and no changed caller supplies a public address; single-panel late failures have dedicated recovery and reconciliation. I found no provider-seat, database-guard bypass, text-first, or machine-specific-path drift in the changed runtime paths.

Tests: the exact command was invoked but could not start because the read-only review environment provides no writable temporary directory. Therefore **0 tests ran**. A collection-only pass found **209 tests**; the branch’s claimed “209 passed” result was not independently verified here.

**VERDICT: NO-GO**
