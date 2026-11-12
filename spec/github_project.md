# GitHub Project orchestration layer

A GitHub Project (v2) is the shared work queue for every agent: issues are the units of work, native relationships are the dependency graph, and a few custom fields say who is doing what, where, and how far it has got. The rules below are generalized from a live multi-agent project (~700 issues across four repositories, ten coding agents); the lessons it taught are in [Lessons from a live project](#lessons-from-a-live-project).

## Repository model

The **main repo** hosts the GitHub Project and pins every work repository as a git submodule. Each submodule keeps its own history, issues and `AGENTS.md`; together they behave like a mono-repo without being one.

- An issue lives in the repository that owns the code it changes. Umbrella/coordination issues live in the main repo.
- A change inside a submodule is merged in that repository first; the main repo then updates the pin in its own reviewed PR. Never bump pins as routine cleanup.
- Link the project README to specs on the default branch, never a feature branch.

## Issues and dependencies

- One issue per unit of work an agent can finish and verify. Large scopes become a **parent issue with native sub-issues**; the parent tracks integration, children carry the work.
- Ordering uses **native `blocked_by` edges**, not prose, labels or a status value. The orchestrator's ready queue is: open, `Planned`, Agent empty, every blocker closed.
- Classify with one project field (e.g. `Workstream`, single select) and optionally a `[Type]` title prefix. Do not duplicate Status in labels.

```sh
gh api 'repos/OWNER/REPO/issues/N/dependencies/blocked_by?per_page=100' --paginate
gh api -X POST repos/OWNER/REPO/issues/N/dependencies/blocked_by -F issue_id=<REST id of blocker>
gh api -X POST repos/OWNER/REPO/issues/PARENT/sub_issues -F sub_issue_id=<REST id of child>
gh issue transfer N OWNER/OWNING_REPO -R OWNER/CURRENT_REPO
```

REST endpoints take the numeric issue `id`; project items/fields take GraphQL node IDs. Issue numbers change on transfer: update every reference afterwards.

## Custom fields

| Field | Type | Rule |
| --- | --- | --- |
| **Status** | single select | `Planned` → `Research` → `Spec-reviewed` → `Building` → `AI-reviewed` → `Human-reviewed`. Stages only; there is no `Blocked` or `Done` option. New issues start Planned; move it the moment work starts. Only a human sets Human-reviewed. |
| **Agent** | single select | The agent **currently** working the issue. Set before substantive work; **clear it** on finish, pause, handoff or waiting for review. Empty means nobody is working. Not an authorship record. |
| **Path** | text | `<repo>/<repo-relative/path>` — exactly one path, forward slashes, no notes. Use the stable folder that owns the work (not a single file, not a broad parent). Set at start; keep it after Agent clears. |
| Start date / End date | date | Optional. Start date = when work first started (set with the first non-Planned Status); End date = when it reached AI-reviewed. Needed only for roadmap views. |
| Workstream | single select | Optional classification, independent of Status. |

**Agent option naming:** one option per agent *harness* identity (Claude, Codex, Cursor, Copilot, Kiro, Antigravity, ZCode, OpenCode, Kilo, Muse, plus web targets such as Gemini, Grok, ChatGPT), matching the `id`s in `agents.json`. Do not mix in model-family names; the model goes in the claim comment.

**Status vs. open/closed:** the issue's open/closed state and Status are separate and must agree:

| Issue state | Allowed Status |
| --- | --- |
| Open | Planned, Research, Spec-reviewed, Building |
| Closed as completed | AI-reviewed, Human-reviewed |
| Closed as not planned | any; it is ignored by the ready queue |

## Start / finish sequence

1. Read the issue, its Agent, Status, Path, blockers and recent comments. If another agent holds it, coordinate through the mailbox instead of taking over.
2. If the issue is in the wrong repository, transfer it first.
3. Set Path, Status (Research or Building) and Start date if empty; register Agent.
4. Post a **claim comment**: agent and model, date, branch and worktree, scope, planned checks. Agents usually post through the owner's GitHub account, so the comment text — not its author — records which agent acted.
5. Work on a feature branch; post progress comments with the head commit and the evidence (check names and results, report paths).
6. On finish or pause: set Status to the stage actually reached, close the issue only if it reached AI-reviewed, **clear Agent**, then re-read the fields to verify every write.

```sh
gh project field-list NUMBER --owner OWNER --format json
gh project item-list NUMBER --owner OWNER --format json --limit 2000   # default limit is 30; large projects exceed 500
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id STATUS_FIELD_ID --single-select-option-id OPTION_ID
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id AGENT_FIELD_ID --single-select-option-id OPTION_ID
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id AGENT_FIELD_ID --clear
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id PATH_FIELD_ID --text 'repo/path'
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id START_DATE_FIELD_ID --date YYYY-MM-DD
```

Resolve field and option IDs from the live project every time; never reuse IDs from chat or old notes. `gh` needs the `project` scope (`gh auth refresh -s project`). For bulk reads use one paginated GraphQL query (`gh api graphql --paginate`) that fetches field values, issue state, `blockedBy`, `subIssues` and `parent` together, instead of one call per issue.

## Views

| View | Layout | Purpose |
| --- | --- | --- |
| Status | Board by Status, `is:open` | Pipeline |
| Agent | Board by Agent, `is:open` | Who is working on what right now |
| Backlog | Table grouped by Agent | Triage |
| Roadmap | Roadmap on Start/End date | Timeline (only meaningful if dates are kept) |

## Drift audit

Run before dispatching work and after any bulk change. Each is a real failure seen in practice:

| Check | Fix |
| --- | --- |
| Agent set on a closed or AI-reviewed issue | Stale claim: clear Agent |
| Agent set but no claim comment or commit activity recently | Ask the agent via mailbox; clear if abandoned |
| Repository in Path ≠ issue's repository | Transfer the issue to the owning repo |
| Path not matching `^[^/\s]+/[^\s]+$` or pointing at a missing path | Correct it from the actual working tree |
| Open issue with AI-/Human-reviewed, or closed-completed issue still Planned/Building | Reconcile state and Status (table above) |
| Non-Planned Status without Start date, or Planned with a Start date | Set or clear the date |
| Label that mirrors a status disagreeing with Status | Delete the mirroring label |
| Parent closed while a sub-issue is open, or a blocker open while the dependent is AI-reviewed | Reopen or re-scope |

## Lessons from a live project

Generic lessons from operating this model with many agents in parallel:

1. **Ownership drifts unless clearing is part of "done".** About half of the Agent values on the board belonged to finished issues. Agents remember to claim but forget to release; the finish step must clear Agent and the audit must catch stale claims, otherwise the Agent board stops meaning "who is busy".
2. **Issues are created where the planner happens to be, not where the work lives.** Over a hundred issues sat in the umbrella repo while their Path pointed into a submodule. Compare the issue's repository with the first segment of Path, and transfer before work starts.
3. **A `Blocked` status competes with native dependencies and loses.** Hundreds of issues had native `blocked_by` edges; the one item using a Blocked status had none. Keep blocking in the graph, and derive "ready" from it.
4. **Anything stored twice diverges.** Labels that copied the status (e.g. `timeline:building`) disagreed with the Status field on a noticeable share of issues, and Status disagreed with open/closed state in both directions. Keep one source of truth per fact and audit the rest.
5. **Free-text fields get used for notes.** Path held single files, broad parents and even parenthetical explanations. Constrain the format and put commentary in the issue.
6. **Optional fields are only useful if the workflow sets them.** Start dates were missing on active items and an iteration field was used once. Either make a field part of the start/finish sequence or remove it.
7. **The comment trail is the real evidence.** The useful issues have a claim comment (agent, branch, worktree, scope, planned checks) and progress comments citing commits and check results. Status alone never proves verification.
8. **Name the agent, not the model.** Mixing harness names and model names in the Agent field makes it unclear which tool to message or which credentials it used.
9. **Plan for scale.** Hundreds of items exceed CLI defaults; always paginate and batch reads through GraphQL, and re-read after writes.
10. **Backlog dominates.** Most items stay Planned, many of them blocked. Dispatch from the ready queue (unblocked, unassigned) instead of scanning the board.
