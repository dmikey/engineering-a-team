# Hypervisor Autonomy Proof - 14 Day Aggressive Execution Plan

Date baseline: 2026-08-03
Operating mode: canary-first, safe-label-only auto-merge, immediate Copilot + Council escalation on unresolved conflicts.
Primary success gate: decision quality and safety.

## Mission
Prove that the hypervisor CLI + CI control loop can autonomously manage PR lifecycle and agent orchestration without unsafe merges, runaway workflow dispatch, or hidden failures.

## Non-Negotiable Safety Gates
1. Unsafe auto-merges: 0.
2. False escalations: < 5% of all escalations.
3. Unresolved conflict aging: no PR in unresolved conflict state > 24h without escalation artifact.
4. Dispatch storms: never exceed configured hourly dispatch budget.
5. Token/auth blind spots: 0 runs where auth failed without explicit alert artifact.

## Daily Scorecard (track every day)
- PRs observed, acted on, merged, escalated, held.
- Draft to ready transitions attempted/succeeded.
- Conflict sync attempts, Copilot handoffs, Council escalations.
- Workflow dispatch count vs budget.
- Model mode: full token, fallback token, heuristic mode.
- Incidents opened/closed and MTTR.

## Day-by-Day Plan

### Day 1 - Lock scope and safety envelope
Objective: freeze validation boundaries and rollback triggers.
Deliverables:
- Validation charter issue with mission, scope, exclusions, and gates.
- Rollback trigger list pinned in a team discussion.
- Canary repository and branch policy confirmed.
Actions:
1. Create issue: "Hypervisor 14-day validation charter".
2. Create discussion: "Hypervisor runbook and rollback triggers".
3. Confirm safe-label semantics for auto-merge.
Exit criteria:
- Charter accepted by maintainers.
- Rollback triggers approved and visible.

### Day 2 - Instrument decision ledger schema
Objective: make every orchestration decision auditable.
Deliverables:
- Decision ledger schema documented (action, reason, trace_id, outcome).
- Logging path chosen for local and CI modes.
- Test stubs for schema validation.
Actions:
1. Add ledger format doc under docs.
2. Identify heartbeat decision points to emit ledger entries.
3. Add unit test skeletons for event shape and required fields.
Exit criteria:
- 100% of decision types mapped to ledger event types.

### Day 3 - Implement decision ledger in heartbeat flow
Objective: persist decision evidence for all key actions.
Deliverables:
- Heartbeat emits ledger events for mark_ready, merge, sync_branch, run_qa, send_back_to_copilot, dispatch_workflow.
- Trace id propagated through action execution.
- Tests covering event emission paths.
Actions:
1. Implement event writer in heartbeat runner.
2. Attach trace id to each generated plan item.
3. Add tests for event emission and failure outcomes.
Exit criteria:
- No action executes without a ledger event.

### Day 4 - Enforce safe-label-only merge policy
Objective: block autonomous merge unless explicit safety label is present.
Deliverables:
- Merge precondition check in heartbeat policy.
- Negative tests proving unlabeled PR cannot auto-merge.
- Positive tests proving labeled eligible PR can proceed.
Actions:
1. Add merge-gating predicate.
2. Update verdict/action selection tests.
3. Add policy rationale to docs.
Exit criteria:
- 0 autonomous merges in test matrix without safe label.

### Day 5 - Harden conflict escalation chain
Objective: deterministic conflict handling and dedupe.
Deliverables:
- Flow: sync attempt -> Copilot handoff -> Council escalation -> issue tracking.
- Cooldown/dedupe protections verified.
- Escalation reason taxonomy documented.
Actions:
1. Add tests for repeated conflict cycles.
2. Verify single escalation artifact per stuck PR window.
3. Validate Council trigger rules for repeated failures.
Exit criteria:
- Repeated conflicts do not spam issues/discussions.

### Day 6 - Token and degraded-mode reliability
Objective: guarantee safe behavior under token failure.
Deliverables:
- Explicit test matrix for MODELS token states.
- Auth failure alert behavior defined and tested.
- Heuristic mode safety constraints documented.
Actions:
1. Add tests: valid token, invalid token, missing token.
2. Verify fallback mode still obeys merge gate and escalation policy.
3. Ensure auth failures generate visible artifacts.
Exit criteria:
- No silent auth/model failures.

### Day 7 - Dispatch budget and cooldown protections
Objective: prevent workflow storms and action thrash.
Deliverables:
- Tests for max dispatch budget behavior.
- Tests for per-action cooldown behavior.
- Dashboard/readout field for budget remaining.
Actions:
1. Add high-volume simulation test for dispatch count.
2. Verify budget clamp under mixed action demand.
3. Add operator-facing budget status output.
Exit criteria:
- Budget exceeded path always degrades to hold/wait.

### Day 8 - Unify hypervisor control plane behavior
Objective: align local CLI and CI orchestration decisions.
Deliverables:
- Shared decision path contract documented.
- Local runner and workflow dispatch entry points mapped to same policy semantics.
- Smoke test for equivalent behavior on same PR snapshot.
Actions:
1. Define control-plane interface in docs.
2. Wire CLI mode to use same gating assumptions as CI.
3. Add parity test notes.
Exit criteria:
- Same input snapshot yields same action plan in local and CI mode.

### Day 9 - Discussion participation via Council path
Objective: ensure the system participates in team discussions through escalation outcomes.
Deliverables:
- Council output format for discussion updates.
- Evidence that escalations produce actionable discussion artifacts.
- Dedupe logic for repeated council posts.
Actions:
1. Validate council output channel and message template.
2. Add tests/fixtures for escalation commentary text.
3. Confirm links between PR, issue, and discussion artifacts.
Exit criteria:
- Every council escalation is visible and traceable in discussion artifacts.

### Day 10 - End-to-end canary dry-run
Objective: rehearse full autonomy logic with mutations disabled where needed.
Deliverables:
- 24h dry-run report with scorecard metrics.
- List of policy misses and reliability defects.
- Fix queue prioritized by safety impact.
Actions:
1. Run heartbeat cadence in canary dry-run mode.
2. Collect ledger events and summarize by action type.
3. Open/triage defects from anomalies.
Exit criteria:
- No critical safety violations in dry-run.

### Day 11 - Canary live window A (controlled mutations)
Objective: enable safe-label merges and escalation actions in canary.
Deliverables:
- First live-window operations report.
- Incident log with response times.
- Metrics delta from dry-run baseline.
Actions:
1. Enable controlled mutation toggles.
2. Monitor every cycle and reconcile scorecard.
3. Trigger rollback immediately on hard-stop condition.
Exit criteria:
- Safety gates still passing after first live window.

### Day 12 - Canary live window B (stress scenarios)
Objective: validate behavior under high PR churn and mixed failure conditions.
Deliverables:
- Stress-run report (queue depth, dispatch budget pressure, escalation rate).
- Confirmed behavior for conflict-heavy PR set.
- Tuned cooldown/budget values if required.
Actions:
1. Replay or simulate high-volume PR states.
2. Verify no dispatch storms and no unsafe merges.
3. Apply safe parameter tuning if justified by evidence.
Exit criteria:
- System remains stable with high churn; safety gates unaffected.

### Day 13 - Readiness review and go/no-go package
Objective: assemble final proof evidence.
Deliverables:
- Evidence pack: metrics, incidents, test results, artifact links.
- Go/no-go recommendation with explicit rationale.
- List of mandatory pre-prod fixes (if any).
Actions:
1. Compare outcomes against Day 1 success gates.
2. Summarize residual risks and mitigations.
3. Present recommendation to maintainers.
Exit criteria:
- Stakeholders can make a binary go/no-go decision from evidence.

### Day 14 - Production ramp decision and rollout script
Objective: either expand safely or iterate with targeted fixes.
Deliverables:
- If GO: phased production enablement schedule and ownership map.
- If NO-GO: two-week remediation sprint with prioritized blockers.
- Updated runbook with final operating procedures.
Actions:
1. Execute decision branch (GO or NO-GO).
2. Publish final runbook and schedule.
3. Establish weekly quality/safety review cadence.
Exit criteria:
- Next phase has explicit owners, timeline, and rollback controls.

## Hard-Stop Rollback Triggers
1. Any unsafe autonomous merge.
2. Agent success rate < 70% sustained for two consecutive check windows.
3. Repeated auth/model failures without recovery in two cycles.
4. Duplicate/conflicting merge actions on the same PR within one hour.
5. Council escalation loop or artifact spam.

## Ownership Model (minimum)
- Incident commander: 1 human owner per canary window.
- Automation owner: heartbeat and dispatch integrity.
- Safety owner: merge policy and escalation correctness.
- Evidence owner: daily scorecards and readiness pack.

## Minimum Test Expansion Targets (during 14-day run)
1. PR decision matrix tests: +30.
2. Merge gate and safe-label tests: +15.
3. Conflict escalation + dedupe tests: +20.
4. Token/fallback and auth tests: +15.
5. Dispatch budget/cooldown tests: +20.
6. E2E scenario tests: +10.

## Definition of Concept Success
The concept is proven when the canary run demonstrates autonomous PR lifecycle orchestration and agent coordination with zero unsafe merges, bounded escalation noise, reliable conflict handling, and complete decision traceability for every action.
