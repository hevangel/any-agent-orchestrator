# GitHub Project orchestration layer

A GitHub Project (v2) is the shared work queue for every agent: issues are the units of work, native relationships are the dependency graph, and a few custom fields say who worked on what, where, in what order and how far it has got. The rules below are generalized from a live multi-agent project (~700 issues across four repositories, ten coding agents); the lessons it taught are in [Lessons from a live project](#lessons-from-a-live-project).

## Repository model

The **main repo** hosts the GitHub Project and pins every work repository as a git submodule. Each submodule keeps its own history, issues and `AGENTS.md`; together they behave like a mono-repo without being one.

- An issue lives in the repository that owns the code it changes. Umbrella/coordination issues live in the main repo.
- A change inside a submodule is merged in that repository first; the main repo then updates the pin in its own reviewed PR. Never bump pins as routine cleanup.
- Link the project README to specs on the default branch, never a feature branch.

## Issues and dependencies

- One issue per unit of work an agent can finish and verify. Large scopes become a **parent issue with native sub-issues**; the parent tracks integration, children carry the work.
- Ordering of *what must finish first* uses **native `blocked_by` edges**, not prose, labels or a status value. Ordering of *what to do first among ready work* uses the **Iteration** field.
- The orchestrator's **ready queue** is: open, Status `Planned`, no `wip` or `needs-human` label, every blocker closed, requirement labels satisfiable by an available agent — sorted by Iteration (earliest first; issues with no Iteration last), then by issue number. Agent is not part of the test: it only records who touched the issue last.
- Classify the kind of work with a `[Type]` title prefix **and** the matching type label (see [Labels](#labels)); the two must agree. Group by area with the optional `Workstream` field. Do not duplicate Status in labels.

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
| **Status** | single select | `Planned` → `Research` → `Spec-reviewed` → `Building` → `AI-reviewed` → `Human-reviewed`. Stages only; there is no `Blocked` or `Done` option. New issues start Planned; move it the moment work starts. AI-reviewed is the last stage before release. Human-reviewed comes **after** the release: only a human sets it, once they have used the change in a released version (see [Releases and human review](#releases-and-human-review)). |
| **Agent** | single select | The **last** agent that worked the issue. Set at claim; **keep it** on finish, pause, handoff or review — the next agent overwrites it when it claims. Empty means no agent has touched the issue. Whether anyone is working on it *now* is the `wip` label, not this field. Earlier agents are in the comment trail. |
| **Iteration** | iteration | Implementation order and priority. Set at triage by the planner; an earlier iteration is dispatched first. An issue unfinished when its iteration ends moves to the current or next iteration, never stays in a past one. Within one iteration the lower issue number goes first. |
| **Path** | text | `<repo>/<repo-relative/path>` — exactly one path, forward slashes, no notes. Use the stable folder that owns the work (not a single file, not a broad parent). Set at start; keep it afterwards. |
| Start date / End date | date | Optional. Start date = when work first started (set with the first non-Planned Status); End date = when it reached AI-reviewed. |
| Workstream | single select | Optional classification, independent of Status. |

**Agent option naming:** one option per agent *harness* identity (Claude, Codex, Cursor, Copilot, Kiro, Antigravity, ZCode, OpenCode, Kilo, Muse, plus web targets such as Gemini, Grok, ChatGPT), matching the `id`s in `agents.json`. Do not mix in model-family names; the model goes in the claim comment.

**Iteration setup:** configure the cadence (e.g. one week) in the project's field settings and keep a few future iterations created, so triage can always place an issue.

**Status vs. open/closed:** the issue's open/closed state and Status are separate and must agree:

| Issue state | Allowed Status |
| --- | --- |
| Open | Planned, Research, Spec-reviewed, Building |
| Closed as completed | AI-reviewed, Human-reviewed |
| Closed as not planned | any; it is ignored by the ready queue |

## Workflow labels

Two labels carry transient workflow state. They exist because they can be set by every agent — including those that cannot write project fields — and each has a defined removal step. Create them in every repository that holds issues (`gh label clone OWNER/MAIN_REPO -R OWNER/SUBMODULE_REPO`).

| Label | Meaning | Added | Removed |
| --- | --- | --- | --- |
| `wip` | An agent is working on the issue right now. | Claim | Finish, pause, handoff, or waiting for review |
| `needs-sync` | The latest `aao:` comment has field changes not yet applied to the project. | By an agent without project write access, with that comment | By the agent that applies the fields |

## Labels

Labels hold facts that no project field holds. They are used because every agent can set them (connector-only agents included) and because they show up in plain issue searches across repositories. **Never put Status, Agent, Iteration or Path in a label** — that is a second copy of a field, and it will diverge ([lesson 4](#lessons-from-a-live-project)).

| Group | Labels | Rule |
| --- | --- | --- |
| Workflow | `wip`, `needs-sync` | Transient; see [Workflow labels](#workflow-labels). |
| Type | `bug`, `feature`, `spec`, `docs`, `chore` | Exactly one per issue, matching the title prefix: `[Bug]`, `[Feature]`, `[Spec]`, `[Docs]`, `[Chore]`. Set by whoever creates the issue. |
| Human | `needs-human` | The issue cannot progress without a person: a login or 2FA, a credential, a decision, a purchase. The dispatcher skips it. Remove it once the human has acted. Not used for review — review happens after release. |
| Requirements | `needs:browser`, `needs:desktop`, `needs:gpu`, `needs:secrets`, `os:windows`, `os:linux`, `local-only` | What the machine and channel must offer. The dispatcher sends the issue only to an agent on a machine that satisfies every requirement label. `local-only` excludes cloud agents (Codex cloud, Copilot coding agent, Claude Code cloud, Cursor cloud agents), which cannot reach this computer, herdr or SSH machines. No requirement label means any agent may take it. |
| Effort | `size:s`, `size:m`, `size:l` | Set at triage. Used to route work by cost: small items go to free-token or BYOK agents when quota allows, large ones to `core` agents. |
| Risk | `spends-tokens`, `touches-credentials` | The change runs paid e2e tests or handles credentials. The AI review must name the risk explicitly before the issue reaches AI-reviewed. |

Keep the canonical label set in the main repository and copy it to every submodule repository (`gh label clone OWNER/MAIN_REPO -R OWNER/SUBMODULE_REPO`). Add new labels to the main repository first.

## Milestones

**Iteration says when work is done; a milestone says what ships together.** A milestone is a release of one repository, named after its version (`v0.2.0`), optionally with a short theme in the description and a target date.

- Assign a milestone at triage only when the issue is meant for a specific release; most backlog issues have none until their release is planned.
- Milestones belong to one repository. Work that spans repositories gets a parent issue with sub-issues; each sub-issue gets the milestone of its own repository's release. Do not create same-named milestones across repositories to model a cross-repo goal.
- Do not order work with milestones; that is Iteration's job.

## Releases and human review

The orchestrator follows a **shift-right** approach to quality: agents build and review, the release ships, and humans do QA by using the released version.

1. A milestone is released when every issue in it is closed as completed (AI-reviewed) or moved out of the milestone. Tag the release and generate the changelog from the milestone's closed issues, then close the milestone. There is no human gate before the release.
2. Users run the release. When they are satisfied with a change, a human sets its issue to **Human-reviewed** (the issue stays closed).
3. A bug found in a release becomes a new `[Bug]` issue labelled `bug`, linking the original issue and the release, rather than reopening a released issue. Users are encouraged to fix it with their own AI coding agent and send a PR ([CONTRIBUTING.md](../CONTRIBUTING.md)).

## Agent identity and access

Agents act through different GitHub identities: some through the owner's account, some through another account with write access to the repository. The comment author therefore never identifies the agent. Every claim, progress and release comment starts with one **`aao:` line** that does:

```text
aao: claim agent=codex model=gpt-5.5 status=Building path=orchestrator/src/orchestrator iteration="Iteration 4"
aao: progress agent=codex commit=1a2b3c4
aao: release agent=codex status=AI-reviewed
```

Actions are `claim`, `progress` and `release`. Keys are optional except `agent`; values with spaces are quoted. The rest of the comment is free text (branch, worktree, scope, checks, evidence).

What an agent may write depends on its tools, not on which account it uses:

| Agent has | Writes | How project fields get updated |
| --- | --- | --- |
| `gh` with the `project` scope **and** Write access to the Project | Fields, labels, comments, issue state | Itself |
| Only a GitHub connector/MCP, or `gh` without project access | Labels, comments, issue state | It adds `needs-sync`; any agent with project access (normally the orchestrator) applies the fields from the latest `aao:` line and removes `needs-sync` |

Repository write access does not grant Project access: a non-owner account must also be added to the Project (Settings → Manage access) with Write role before it can edit fields.

## Start / finish sequence

1. Read the issue, its Status, Agent, Iteration, Path, labels, milestone, blockers and recent comments. If it has `wip` and the latest `aao: claim` names another agent, coordinate through the mailbox instead of taking over.
2. If the issue is in the wrong repository, transfer it first.
3. Add `wip`. Post the **claim comment**: the `aao: claim` line, then date, branch and worktree, scope and planned checks.
4. Fields — with project access: set Agent, Path, Status (Research or Building) and Start date if empty. Without: add `needs-sync`; the claim line carries the values.
5. Work on a feature branch; post `aao: progress` comments with the head commit and the evidence (check names and results, report paths).
6. On finish or pause: post `aao: release` with the stage actually reached, close the issue only if it reached AI-reviewed, and remove `wip`. With project access, set Status (and End date when AI-reviewed) and **leave Agent as it is**; without, add `needs-sync`. Re-read the issue and fields to verify every write.

```sh
gh project field-list NUMBER --owner OWNER --format json
gh project item-list NUMBER --owner OWNER --format json --limit 2000   # default limit is 30; large projects exceed 500
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id STATUS_FIELD_ID --single-select-option-id OPTION_ID
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id AGENT_FIELD_ID --single-select-option-id OPTION_ID
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id ITERATION_FIELD_ID --iteration-id ITERATION_ID
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id PATH_FIELD_ID --text 'repo/path'
gh project item-edit --id ITEM_ID --project-id PROJECT_ID --field-id START_DATE_FIELD_ID --date YYYY-MM-DD
gh issue edit N -R OWNER/REPO --add-label wip          # --remove-label wip on finish
```

Iteration IDs are not in `field-list`; read them with GraphQL (use `organization(login:)` for an org-owned project):

```sh
gh api graphql -F owner=OWNER -F number=NUMBER -f query='
query($owner:String!,$number:Int!){ user(login:$owner){ projectV2(number:$number){
  field(name:"Iteration"){ ... on ProjectV2IterationField { id
    configuration { iterations { id title startDate duration } completedIterations { id title } } } } } } }'
```

Resolve field, option and iteration IDs from the live project every time; never reuse IDs from chat or old notes. `gh` needs the `project` scope (`gh auth refresh -s project`). For bulk reads use one paginated GraphQL query (`gh api graphql --paginate`) that fetches field values, labels, issue state, `blockedBy`, `subIssues` and `parent` together, instead of one call per issue.

## Views

| View | Layout | Purpose |
| --- | --- | --- |
| Status | Board by Status, `is:open` | Pipeline |
| Active | Board by Agent, `is:open label:wip` | Who is working on what right now |
| Queue | Table, `is:open status:Planned`, grouped by Iteration | Dispatch order; the "No Iteration" group is untriaged |
| Current | Board by Status, `iteration:@current` | Progress of the current iteration |
| History | Table grouped by Agent | What each agent touched last |
| Releases | Table, `has:milestone`, grouped by Milestone | What each release contains |
| Human review | Table, `is:closed status:AI-reviewed has:milestone` | Released work waiting for a human to try it |
| Roadmap | Roadmap on Iteration | Timeline |

## Drift audit

Run before dispatching work and after any bulk change. Each is a real or expected failure:

| Check | Fix |
| --- | --- |
| `wip` on a closed issue or one whose Status is AI-/Human-reviewed | Stale claim: remove `wip` |
| `wip` but no `aao:` comment or commit activity recently | Ask the agent via mailbox; remove `wip` if abandoned |
| `needs-sync` present, or fields disagree with the latest `aao:` line | Apply the fields from that line; remove `needs-sync` |
| Status past Planned but Agent empty | Set Agent from the latest `aao: claim` |
| Open issue with no Iteration, or in a completed iteration | Triage into the current or a future iteration |
| Repository in Path ≠ issue's repository | Transfer the issue to the owning repo |
| Path not matching `^[^/\s]+/[^\s]+$` or pointing at a missing path | Correct it from the actual working tree |
| Open issue with AI-/Human-reviewed, or closed-completed issue still Planned/Building | Reconcile state and Status (table above) |
| Non-Planned Status without Start date, or Planned with a Start date | Set or clear the date |
| Label that mirrors a status disagreeing with Status | Delete the mirroring label |
| `[Type]` title prefix missing, or not matching the type label (or zero / several type labels) | Fix the prefix and label to agree |
| Label not in the main repository's canonical set | Add it there first, or remove it |
| `needs-human` with no human activity recently | Remind the owner; the issue stays out of the queue |
| Closed milestone with an open issue | Move the issue to the next milestone |
| Human-reviewed on an issue whose milestone is not released | Reset to AI-reviewed |
| Parent closed while a sub-issue is open, or a blocker open while the dependent is AI-reviewed | Reopen or re-scope |

## Lessons from a live project

Generic lessons from operating this model with many agents in parallel:

1. **Ownership drifts unless releasing is part of "done".** About half of the Agent values on the board belonged to finished issues: agents remember to claim but forget to release. So "busy now" is a separate `wip` label that the finish step removes and the audit catches, and Agent is allowed to keep the last agent — the value agents leave behind anyway.
2. **Issues are created where the planner happens to be, not where the work lives.** Over a hundred issues sat in the umbrella repo while their Path pointed into a submodule. Compare the issue's repository with the first segment of Path, and transfer before work starts.
3. **A `Blocked` status competes with native dependencies and loses.** Hundreds of issues had native `blocked_by` edges; the one item using a Blocked status had none. Keep blocking in the graph, and derive "ready" from it.
4. **Anything stored twice diverges.** Labels that copied the status (e.g. `timeline:building`) disagreed with the Status field on a noticeable share of issues, and Status disagreed with open/closed state in both directions. Keep one source of truth per fact and audit the rest.
5. **Free-text fields get used for notes.** Path held single files, broad parents and even parenthetical explanations. Constrain the format and put commentary in the issue.
6. **Optional fields are only useful if the workflow sets them.** Start dates were missing on active items and an iteration field was used once. Either make a field part of the start/finish sequence or remove it — here Iteration is set at triage and drives the ready queue.
7. **The comment trail is the real evidence.** The useful issues have a claim comment (agent, branch, worktree, scope, planned checks) and progress comments citing commits and check results. Status alone never proves verification.
8. **Name the agent, not the model.** Mixing harness names and model names in the Agent field makes it unclear which tool to message or which credentials it used.
9. **Plan for scale.** Hundreds of items exceed CLI defaults; always paginate and batch reads through GraphQL, and re-read after writes.
10. **Backlog dominates.** Most items stay Planned, many of them blocked. Dispatch from the ready queue (unblocked, not `wip`, in Iteration order) instead of scanning the board.
