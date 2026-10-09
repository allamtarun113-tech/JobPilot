# Restore from a backup (design 6.6)

A restored database can hold outbox rows for emails that were already sent after the backup was taken. Restore is a
procedure, not a command - follow every step, in order.

1. **Stop everything.** Stop `make backend`. The dispatcher must not run against the restored data yet.
2. **Restore the database.**
   `uv run python scripts/backup.py --decrypt Data/user/backups/<file>.dump.enc /tmp/jobpilot.dump`
   `pg_restore --clean --no-owner -d "<target database URL>" /tmp/jobpilot.dump` (use the pg_restore matching your server, e.g. `/opt/homebrew/opt/postgresql@16/bin/pg_restore`) then delete `/tmp/jobpilot.dump`.
   For a drill, restore into a new local database (`createdb jobpilot_restore_drill`), never over production.
3. **Migrate** the restored copy: `DATABASE_URL=<restored> make migrate` (`CONFIRM=yes` for a cloud database).
4. **Start the dispatcher paused:** `DISPATCHER_START_PAUSED=1 make dispatcher` (or `make dispatcher-pause` first).
5. **Reconcile:** `make reconcile ARGS="--before <backup time ISO>"` to read the report, then
   `make reconcile ARGS="--before <backup time> --apply --skip-telegram"`. Emails already in Gmail Sent are recorded,
   never resent; Telegram messages from before the backup are skipped (the bot can't read its own history, and a
   duplicate card is worse than a missing one - `/digest` lists every card anyway).
6. **Check:** the dashboard opens, `/health/details` is ok, the outbox shows only what you expect.
7. **Resume:** `make dispatcher-resume`, then start `make backend` normally.

Approvals older than 24 hours are not sent: those cards ask for a fresh tap. PDFs live in the object store (Supabase
Storage or `Data/user/storage`) - back that up separately (Supabase dashboard -> Storage, or copy the folder).
