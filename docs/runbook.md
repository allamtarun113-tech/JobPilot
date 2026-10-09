# JobPilot runbook

One entry per integration and per alert. Each says what you see, what it means, and what to do.
Commands run on the laptop from the repo folder. Start with `make dispatcher-status` and the dashboard **Settings** page.

## Everyday
- Start the backend: `make backend` (worker + dispatcher). Dashboard: your Vercel URL (TOTP login).
- `/start [cap]`, `/pause`, `/resume`, `/stop`, `/status`, `/digest`, `/prefs`, `/killswitch on|off`.
- Shadow mode (`config/defaults.yaml: shadow_mode: true`) logs emails instead of sending them, and by default does
  not create real Gmail drafts either (`shadow_creates_gmail_drafts: false`).

## Alerts (they arrive in Telegram with a ⚠️)
| Alert | Meaning | Do this |
|---|---|---|
| "N of today's token budget used (80%)" | Close to `budgets.tokens_per_day` | Nothing, or raise the budget in config. At 100% runs stop and the kill switch turns on by itself; it lifts the next day. |
| "N of today's jobs-API calls used" | Close to `caps.jobs_api_calls_per_day` | Fewer titles x locations in Preferences, or raise the cap if your plan allows. |
| "<integration> failed 3 times in a row" | Gmail / Telegram / a job source keeps failing | See the integration below. |
| "Run #N has made no progress for 10 minutes" | The run is stuck | `/status`; if nothing moves, restart `make backend` - the next `/start` resumes the run from its checkpoint. |
| "Messages have been waiting to send for N minutes" | The dispatcher is down or paused | Is `make backend` running? `make dispatcher-status`; `make dispatcher-resume` if paused. |
| "A telegram/gmail message is stuck (card:N)" | A message failed in a way retries can't fix; that card's queue is paused | **Outbox recovery** below. |
| "Kill switch engaged automatically (error spike)" | 3 jobs failed in a row | Read `var/worker.log`, fix the cause, then `/killswitch off`. |
| "An approved email was not sent within 24 hours" | Gmail was down or the laptop was off | The card asks for one fresh tap ("Re-approve and send vN"). |
| "This run used more than 1.5x the estimate" | Unusually expensive run | Check the Runs tab trace; lower caps or the Job Finder token cap. |
| "Gmail token refresh is failing" | The Gmail refresh token was revoked or expired | `make gmail-auth` (the OAuth app must be *In production*, see docs/setup-job-alerts.md). |

## Integrations
- **Telegram** - dry-run when `TELEGRAM_BOT_TOKEN` is empty. Bot silent? Check `TELEGRAM_CHAT_ID` is your chat id and the
  worker log has no `getUpdates failed`. Only one process may long-poll a bot: never run `make dev` and `make backend` together.
- **Gmail** - HR email drafts and sends go through the Gmail MCP server (dispatcher only). Token errors: `make gmail-auth`.
  Sends are reconciled against Sent before any retry.
- **Jobs API** - JSearch first, SerpApi as fallback (`SERPAPI_API_KEY`). Every call is counted in `api_usage`.
- **Web search / fetch** - OpenAI web search (`OPENAI_API_KEY`, decision 21). Fetch refuses private addresses, LinkedIn/Naukri/Indeed and pages
  disallowed by robots.txt.
- **OpenAI** - `make llm-ping`. Invalid output is retried once, then the step fails visibly.
- **Supabase / Qdrant** - the worker survives dropped database connections and reconnects; if it can't reach the database
  at all, it keeps retrying and logs `database unreachable`.
- **Langfuse** - traces only export with both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set; emails and phone numbers
  are masked before export. Set trace retention to 30 days in the Langfuse project settings.

## Outbox recovery (a stuck message)
1. Dashboard **Settings -> Stuck messages** (or `make dispatcher-status`).
2. A stuck **Telegram** message: read the error; fix the cause if any (e.g. a bad apply link) and tap **Skip this message** -
   the card's queue continues.
3. A stuck **email send**: never skip or resend blindly. `make dispatcher-pause`, then `make reconcile` (report), then
   `make reconcile ARGS=--apply`. "ALREADY SENT" is recorded; "draft gone, not in Sent" means check Gmail by hand.
   Then `make dispatcher-resume`.

## Data
- Backups: `make backup` (needs `BACKUP_KEY`, from `make backup-key`). Restore: docs/restore.md.
- Delete everything: `uv run python scripts/delete_everything.py` (dry run first).
- Learning links: `make check-links` hides dead resources.

## Rollout
Offline suite (`make check`, `make eval-offline`) -> live evals (`make eval`) -> a shadow week -> canary
(`shadow_mode: false`, caps 5 cards and 2 emails a day) -> raise caps a step at a time, a few days apart.
