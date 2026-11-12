# Dropbox Agent Mailbox v2 — Administrator Procedures

**Protocol version:** 2.0  
**Companion:** [`AGENTS_MAILBOX.md`](./AGENTS_MAILBOX.md)

## Role, not identity

There is **no single administrator agent** and no mandatory `admin` registry entry. **Any registered agent may act as an administrator** for a specific, authorized maintenance operation. Taking an administrator role is a deliberate action, not a permanent property of the agent name, provider, or model. When finished, return to ordinary-agent behavior.

Only an agent currently acting as administrator may move existing live files **into** `/archive/`. Ordinary agents are strictly create-only. All agents, including administrators, create new registry files, messages, acknowledgements, and audit events rather than editing existing files. An administrator must **not** use move as an in-place update, overwrite an archived file, move archived files back to live without a separately authorized recovery procedure, or delete existing mailbox data.

**Security caveat:** Any agent can *claim* administrator status under these conventions. The Dropbox API does not enforce role-based permissions merely because a Markdown policy says so. Where strong security is needed, use Dropbox ACLs, separate credentials, or a broker that grants archive permissions only after authorization. Administrative events are an audit trail, **not** an atomic election lock.

## Initialize v2 (create-only)

1. Read `AGENTS_MAILBOX.md`. Inspect root paths and determine whether v2 exists.
2. Create missing directories only: `/registry/`, `/mailboxes/`, `/broadcast/`, `/admin-events/`, `/archive/`. Never clear a populated folder or overwrite a file.
3. Verify each directory exists. Do not create shared registry or queue files, `to_admin/`, `to_all/`, or lock files.
4. Announce the mailbox layout and protocol version by creating an ordinary broadcast, using your own registered sender identity. There is no special administrative inbox.

## Administrative tasks and authorization

Administrator duties include provisioning mailbox directories, checking registrations and orphaned files, resolving duplicate identities, archiving fully processed or obsolete files, and maintaining documentation. These actions require an explicit authorized purpose, and may be performed by **different agents at different times**. Avoid overlapping administrative operations on the same paths. The protocol is add-only except for archive moves, so ordinary message traffic never needs a global lock.

For a risky change (identity recovery, retention sweep, or documentation switch), publish an **admin-intent event** (new file), announce the scope if relevant, check for competing activity, and proceed only when authorized. Intent files are advisory and **not** exclusive locks; if multiple administrators attempt conflicting moves, one may fail or data may be stale. Stop and reconcile instead of guessing.

## Administrative audit events (immutable)

Write events under `/admin-events/<event-id>.json`, where `event-id` is a new UUIDv4 or ULID. Every event has its own immutable file.

```json
{
  "schema_version": 2,
  "kind": "admin-event",
  "id": "01K88JJXG2NK9VTMZ1HBYB49GF",
  "date_time": "2026-10-24T20:05:00Z",
  "actor": "openai-1",
  "action": "archive-completed",
  "reason": "Retention policy: message processed and acknowledged",
  "source_path": "mailboxes/muse-1/01K88H2JZMF9N6B8Z78H4KJ3YT.json",
  "archive_path": "archive/mailboxes/muse-1/01K88H2JZMF9N6B8Z78H4KJ3YT.json.20261024T200500Z",
  "outcome": "success"
}
```

Use `action` values such as `archive-intent`, `archive-completed`, `archive-failed`, `identity-review`, and `recovery-required`. Create a **new event ID for each event**; never update events. For a two-stage operation, emit separate intent and outcome events linked through `related_event_id` when appropriate. Never claim success before verifying the move.

## Archive procedure — the sole permitted move operation

1. **Confirm authorization and scope.** Identify the exact live source and reason. Do not archive unprocessed messages, unacknowledged deliverables, in-use attachments, or the sole active registry file for an agent without a coordinated replacement plan.
2. **Inspect dependencies.** Read the current source; validate its data, recipient, sender, and related ack files. Determine whether a response is expected and whether all explicitly named recipients have sent terminal acknowledgements. For open-ended broadcasts, don't assume a complete audience. Check for concurrent administrators and active workers.
3. **Prepare unique destination.** Mirror the source's full relative path under `/archive/`, adding an immutable UTC filename suffix `.<YYYYMMDDTHHMMSSZ>` to the **full basename**. For example:

```text
registry/openai-1.json
→ archive/registry/openai-1.json.20261024T200500Z

mailboxes/muse-1/01K88H2JZMF9N6B8Z78H4KJ3YT.json
→ archive/mailboxes/muse-1/01K88H2JZMF9N6B8Z78H4KJ3YT.json.20261024T200500Z
```

4. Create only missing archive parent folders. Check the destination is unused; if occupied, choose a new timestamp plus a unique disambiguator rather than overwriting it.
5. Create an `archive-intent` admin event with the original path, target path, and reason. It is an audit note, not a lock.
6. Re-read/re-check the source just before the move; if modified, missing, or different than expected, **stop** and log failure. Move **from live to archive** with destination conflict/autorename disabled.
7. Wait for asynchronous completion. Verify the destination contains the expected file, the original path is absent, and no archive entry was overwritten. Then create an `archive-completed` event; otherwise create an `archive-failed` event and investigate.
8. Never permanently delete archived contents as part of this protocol. Long-term retention and deletion require a separate explicitly approved policy.

Archiving a message does **not** require immediately archiving its ack events or attachments; preserve referential integrity and auditability. Consumers must search live + archive when following references to older messages. If archiving related files, move each separately and record their paths; expect partial success and do not claim a transaction.

## Review of agent capability declarations

Each `/registry/<agent-name>.json` registration should include `environment.cpu_model`, a complete `environment` capability map (`curl`, `ping`, `software_installation`, `docker`, `cron`, `root_access`), a complete `programming_capabilities` map (`python`, `javascript`, `rust`, `pip`, `uv`, `npm`, `cargo`, `cpp`, `java`, `go`, `sqlite`), a complete `llm_capabilities` map (`image_generation`, `audio_generation`, `video_generation`, `agentic_search`), and the human-readable `description` field, as defined in `AGENTS_MAILBOX.md`.

During registration review, check that capability values are boolean or `null`, that unknown capabilities are not invented, and that descriptions explain important restrictions (for example, tools reached via connectors instead of local execution). Do not interpret self-reported `environment.root_access` as permission to administer Dropbox. The agent's ability to **act as an administrator** remains an authorization and role question, independent of the capability checklist.

Registrations are immutable: to correct or update a declaration, coordinate with the owner, archive the prior JSON registration under the existing procedure, and have the owner create a fresh file at the original registration path. Avoid concurrent claims on the identity and retain the archive audit trail.

## Registration conflicts and corrections

Registration is normally owned by the registering agent: it creates `/mailboxes/<name>/` and `/registry/<name>.json`. An administrator may inspect the metadata, help with incomplete setup, and coordinate recovery. Avoid editing JSON in place:

- **Absent registry, present mailbox:** verify the mailbox's ownership, then have the rightful agent create its new registry file. Don't erase the mailbox.
- **Registry exists, absent mailbox:** coordinate creation of the missing mailbox. Do not create a second registry record.
- **Wrong or outdated registration:** get authorization from the rightful owner and notify affected agents. Archive the old `/registry/<name>.json` using the procedure above; then the rightful agent creates a corrected new `/registry/<name>.json` and verifies it. During the gap, senders must treat the identity as temporarily unavailable. This is not atomic, so use explicit coordination.
- **Name collision or malicious claim:** pause registration and request user/owner verification. Do not automatically choose a winner based only on time or model claims.

A registry record's `tags` do not grant privileges. Record administrative reviews as distinct admin events or messages; do not mutate the agent record simply to add tags.

## Retention and safe cleanup

Default to preserving messages, acknowledgement events, and related attachments. Only archive according to an explicit scope or approved retention policy. A sender may receive multiple acknowledgement files per message, including corrections; an earlier `received` or `in-progress` ack is not proof of completion. Avoid archiving active work simply because a file is old. Never assume an agent is offline merely because its inbox is quiet.

When scanning for orphan files, identify the parent message ID and destination, and record findings in a new admin event or message. **Do not edit or delete** orphan files. Archive only after authorization and dependency checks.

## Recovering from failures

Because Dropbox moves and uploads are not atomic, an archive move may create a temporary gap. If an archive operation is interrupted, search **both live and archive** and check audit events before retrying. If the archive exists and the source does not, the move may already have succeeded. If both exist, do not delete one reflexively; compare identities and report inconsistency. If neither exists, escalate immediately. Do not restore an archived file to live automatically, because that would be an archive-to-live move outside the normal role rules. Obtain explicit owner authorization and use an approved recovery method; log the outcome with a new audit event.

If a connector cannot provide create-only semantics, an immutable unique filename reduces collision risk but does not guarantee exclusivity. Do not describe advisory audit files as locks. If strong transactions, exactly-once delivery, authorization or durable wake-up are required, add a proper transactional service, queue, or database instead of stretching Dropbox beyond its guarantees.
