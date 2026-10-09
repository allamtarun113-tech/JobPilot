# Local verification and staged live testing

The application remains in shadow mode (`config/defaults.yaml: shadow_mode: true`). Shadow sends consume the email cap and record the dry-run result, but do not contact Gmail's send endpoint. Draft creation still writes a Gmail draft once Gmail is connected. The user applies to jobs and approves exact resume/email versions.

## Before adding keys

Run `make check` and `make eval-offline`. The checks use `jobpilot_test`, clear its application/checkpoint tables, and must never target the development or production database. Tests ignore the developer's `.env` and use fake providers. Daily caps use UTC day boundaries, matching `core/clock.py`; user-facing scheduling can still use local time. The effective limit is the smaller of the configured rollout cap and the user's preference.

Card builds reserve capacity before model work. Their reservations renew while the build runs and expire after a worker crash. The insert transaction checks ownership again. Resume PDFs use unique attempt keys; conflicting edits can leave unreferenced private objects but cannot replace a committed PDF. Do not remove objects by age alone: retain every key referenced by `resume_doc` or `resume_versions`.

## Adding keys and testing

1. Model tasks use OpenAI `gpt-4o-mini`; set `OPENAI_API_KEY` and run `make llm-ping` to verify it with one paid call. Put credentials in the local `.env`, never chat, source files or logs. Use `.env.example`. Set a Telegram chat ID for your private bot. Enable dashboard TOTP before any non-loopback access. Configure separate MCP caller keys and `APPROVAL_TOKEN_KEY` before live sending.
2. Keep shadow mode enabled. Connect Gmail with `make gmail-auth`; draft creation is a real mailbox write, even in shadow mode. Confirm the profile yourself.
3. Run a small `/start 1` with real provider credentials. Review source matches, scores, grounded evidence, the rendered resume, stored versions and Telegram callbacks. Verify `/stop` and repeated delivery behavior. Measure recall with jobs you share in Telegram; synthetic fixtures are not evidence of real-world recall.
4. Run `make eval`, then `make eval-prep-live`. The latter makes paid model calls against held-out synthetic sources and evaluates required facts, contradictions and citations. Read the actual generated answers too; these small automatic gates do not establish general model quality. Job Chat's offline runner checks prompt boundaries only; separately exercise real adversarial chat requests and verify that profile/preference changes remain pending proposals.
5. Verify Gmail Sent lookup against an authorized test message before relying on crash reconciliation. Then inspect email drafts, exact resume binding, reconfirmation, daily caps and company cooldown. Shadow mode marks a rehearsal as `shadowed`, not sent; it does not insert the sent ledger.
6. Only after those checks and explicit go-live authorization: deploy, run a shadow observation period, then use a small canary cap. Changing keys alone does not enable live email sending.

## Outbox recovery

Use `make dispatcher-pause` before investigating an uncertain send. Query the row's `last_error`, approval and email/card versions using a trusted owner connection; do not print email bodies or secrets into shared logs. A `dead` row blocks only its partition. Normal expired approvals become `skipped` and `needs_reconfirm`, so they do not block later notifications. Reapproving creates a fresh approval/send key; Gmail Sent lookup runs before any new send, including after expiry.

If a send is dead after an ambiguous provider failure, reconcile its deterministic Message-ID in Gmail Sent first. If already accepted, record the external message ID and the sent ledger rather than resend. If not accepted, check the existing Gmail draft and approval age. Never blindly change a dead send to pending or fabricate an approval. Resume the dispatcher with `make dispatcher-resume` only after reconciliation. A reduced cap or kill switch keeps reserved sends pending; if retries exhaust, inspect/reconcile them as above.

For local development, `scripts/dispatcher_ctl.py` shows paused state and backlog. Automated destructive recovery is deliberately absent; it cannot safely distinguish an unobserved send from a missing send.

## Backup and restore drill

Pause the dispatcher and stop workers. Back up the database with `pg_dump --format=custom` using a protected connection configuration, including checkpoint tables, approval ledger, sent ledger, outbox and reservation flags. Independently back up the private object store and resume templates/configuration. Encrypt backup media and restrict access. Verify restore in an isolated database/account: never point a restored dispatcher at live Gmail or Telegram.

On the restored copy, run migrations and check drift, verify every referenced resume key is readable, verify record counts, and run the test suite only against a separate `_test` database. Reconcile all uncertain sends against the actual mailbox before attaching live credentials. Confirm older approvals require reconfirmation. Start live processes with `DISPATCHER_START_PAUSED=1`, then resume deliberately after validation. A successful drill requires evidence of restored PDF bytes, checkpoint continuation and no duplicate external sends. This drill has not yet been performed against cloud services.

## Privacy and retention

See `docs/processors.md`. Keep raw alert failures and traces private. Review and remove them when no longer needed. Removing private data requires stopping all processes, deleting application/checkpoint rows and user object-store files, revoking provider tokens, and deleting backups according to your retention decision. Do not delete reference files in `Data/books`, `Data/learning` or `Data/taxonomy`. `scripts/delete_everything.py` deletes all personal data after two explicit confirmations (dry run by default). Restore: docs/restore.md. Day-to-day: docs/runbook.md.
