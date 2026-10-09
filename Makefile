# JobPilot developer commands. Everything runs through uv.
PY := uv run python
TEST_DB := postgresql+psycopg://localhost/jobpilot_test

.PHONY: gmail-spike approval-keys autostart-install autostart-uninstall reconcile seed-profile backup backup-key check-links contract eval-score eval-guardrails eval-job-chat-live db-target gmail-auth gmail-token-key backend storage-setup import-resume ingest-corpus ingest-learning skill-notes qa-bank knowledge-check rescore resume-trial rebuild-pdfs totp-secret help setup db-create migrate dev http worker dispatcher test lint typecheck fmt eval eval-job-finder replay \
        fake-update dry-run-log llm-ping dispatcher-pause dispatcher-resume dispatcher-status check migrations-check eval-offline eval-prep-live

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: ## install dependencies, create databases, run migrations
	uv sync --all-groups
	$(MAKE) db-create migrate
	DATABASE_URL=$(TEST_DB) $(MAKE) migrate

db-create: ## create the dev and test databases (local Postgres)
	-createdb jobpilot
	-createdb jobpilot_test

db-target: ## show which database DATABASE_URL points at (host only)
	@$(PY) -c "from jobpilot.core.config import get_settings as g; from urllib.parse import urlsplit as u; print('database host:', u(g().psycopg_dsn).hostname or 'local socket')"

migrate: db-target ## apply database migrations + LangGraph checkpoint tables (asks before touching a cloud DB)
	@$(PY) -c "import sys; from jobpilot.core.config import get_settings as g; from urllib.parse import urlsplit as u; h=u(g().psycopg_dsn).hostname or ''; sys.exit(0 if h in ('', 'localhost', '127.0.0.1') or '$(CONFIRM)'=='yes' else 'Refusing to migrate a non-local database. Back it up, read the new migration, then: make migrate CONFIRM=yes')"
	uv run alembic upgrade head
	$(PY) scripts/setup_checkpointer.py
	$(PY) scripts/db_lockdown.py

dev: ## run all three processes under supervisord (logs in var/)
	uv run supervisord -n -c deploy/supervisor.conf

gmail-token-key: ## print a new GMAIL_TOKEN_KEY for .env (encrypts the stored Gmail refresh token)
	@$(PY) -c "from cryptography.fernet import Fernet; print('GMAIL_TOKEN_KEY=' + Fernet.generate_key().decode())"

gmail-auth: ## one-time Gmail sign-in (HR email drafts and sending); stores the refresh token encrypted
	$(PY) -m jobpilot.servers.gmail.oauth_setup

gmail-spike: ## send one test email to yourself and check the Sent lookup used for exactly-once
	$(PY) scripts/gmail_sent_spike.py

autostart-install: ## start the laptop backend at login and keep it running (macOS launchd)
	@mkdir -p var $(HOME)/Library/LaunchAgents
	@sed "s#REPO#$(CURDIR)#g" deploy/launchd/com.jobpilot.backend.plist > $(HOME)/Library/LaunchAgents/com.jobpilot.backend.plist
	-launchctl bootout gui/$$(id -u)/com.jobpilot.backend 2>/dev/null
	@sleep 3
	launchctl bootstrap gui/$$(id -u) $(HOME)/Library/LaunchAgents/com.jobpilot.backend.plist
	@echo "backend will start at login; logs in var/worker.log and var/dispatcher.log"

autostart-uninstall: ## stop auto-starting the laptop backend
	-launchctl bootout gui/$$(id -u)/com.jobpilot.backend
	rm -f $(HOME)/Library/LaunchAgents/com.jobpilot.backend.plist

backend: ## laptop backend: worker + dispatcher only (dashboard on Vercel)
	uv run supervisord -n -c deploy/supervisor-backend.conf

storage-setup: ## create the private Supabase Storage bucket (needs SUPABASE_URL + service key)
	$(PY) -c "import asyncio; from jobpilot.integrations.storage import get_store; s = get_store(); asyncio.run(s.ensure_bucket()) if hasattr(s, 'ensure_bucket') else print('local store - nothing to do'); print('ok')"

http: ## run only the dashboard (http://127.0.0.1:8765)
	$(PY) -m jobpilot.web.main

worker: ## run only the worker
	$(PY) -m jobpilot.worker.main

dispatcher: ## run only the dispatcher
	$(PY) -m jobpilot.dispatcher.main

test: ## all tests (uses the jobpilot_test database)
	DATABASE_URL=$(TEST_DB) uv run pytest -q

lint: ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## mypy over src
	uv run mypy src --ignore-missing-imports

migrations-check: ## fail if models and migrations differ (test database)
	DATABASE_URL=$(TEST_DB) uv run alembic check

check: lint typecheck migrations-check test ## everything CI will run

fmt: ## auto-format
	uv run ruff check --fix .
	uv run ruff format .

eval: ## run the paid/live evals (needs OPENAI_API_KEY; search/jobs keys for some)
	$(PY) -m evals.runners.extraction
	$(PY) -m evals.runners.job_finder
	$(PY) -m evals.runners.tailoring
	$(PY) -m evals.runners.prep
	$(PY) -m evals.runners.job_chat --live

eval-score: ## score agreement with your Approve/Skip decisions (needs 50+ decisions)
	$(PY) -m evals.runners.score

eval-guardrails: ## attack JDs, SSRF URLs, LaTeX payloads, fabricated claims - 100% must be blocked
	$(PY) -m evals.runners.guardrails

eval-job-chat-live: ## 50 Job Chat requests through the model; gate: right tool >= 95%
	$(PY) -m evals.runners.job_chat --live

eval-job-finder: ## run Job Finder quality/security gates (quality needs search + OpenAI keys)
	$(PY) -m evals.runners.job_finder

replay: ## re-score a run's jobs with today's code and profile, no model calls: make replay RUN=12
	$(PY) scripts/replay_run.py $(RUN)

reconcile: ## restore step: check pending emails against Gmail Sent (dispatcher paused). ARGS=--apply to record
	$(PY) scripts/reconcile.py $(ARGS)

seed-profile: ## add draft profile items from Data/user/profile_seed.yaml (confirm them at /profile)
	$(PY) scripts/seed_profile.py $(FILE)

approval-keys: ## print an Ed25519 key pair for Option B send tokens (APPROVAL_SIGNING_KEY / APPROVAL_VERIFY_KEY)
	@$(PY) -c "from jobpilot.safety.approval_tokens import new_signing_keypair as k; p, v = k(); print('APPROVAL_SIGNING_KEY=' + p); print('APPROVAL_VERIFY_KEY=' + v)"

backup-key: ## print a new BACKUP_KEY for .env (keep a copy off this laptop)
	@$(PY) -c "from cryptography.fernet import Fernet; print('BACKUP_KEY=' + Fernet.generate_key().decode())"

backup: ## encrypted pg_dump of DATABASE_URL into Data/user/backups/
	$(PY) scripts/backup.py

check-links: ## re-check learning-resource links; dead ones are hidden from cards
	$(PY) scripts/check_resource_links.py

contract: ## provider contract tests (CONTRACT_LIVE=1 adds harmless live calls)
	DATABASE_URL=$(TEST_DB) uv run pytest -q tests/contract

import-resume: ## import Data/user/resume.tex as the base resume (+ draft profile items), then render it
	$(PY) scripts/import_resume.py $(ARGS)

ingest-corpus: ## rebuild the heading/page-aware interview-prep corpus in Qdrant
	$(PY) scripts/ingest_corpus.py

ingest-learning: ## rebuild the learning-resources collection in Qdrant from Data/learning
	$(PY) scripts/ingest_learning.py

skill-notes: ## write a note + interview Q&A for each of your skills that has none (ARGS="--gaps 20", "--skills a,b --force")
	$(PY) scripts/write_skill_notes.py $(ARGS)

qa-bank: ## copy the questions of every saved prep set into the Q&A bank
	$(PY) scripts/fill_qa_bank.py

resume-trial: ## try resume v3 tailoring on real jobs; no cards, no messages (ARGS="--jobs 10"); PDFs in Data/user/trials
	$(PY) scripts/resume_trial.py $(ARGS)

rebuild-pdfs: ## rebuild resume PDFs the database refers to but storage lacks (no model calls)
	$(PY) scripts/rebuild_missing_pdfs.py

rescore: ## re-score queued / below-bar jobs with the current rubric (ARGS="--limit 20"); no run active
	$(PY) scripts/rescore_jobs.py $(ARGS)

knowledge-check: ## knowledge-base counts and one timed skill lookup (SKILL=python)
	$(PY) scripts/knowledge_check.py $(or $(SKILL),python)

totp-secret: ## print a new dashboard TOTP secret + authenticator URI (put it in .env)
	@$(PY) -c "from jobpilot.safety import totp; s = totp.new_secret(); print('DASHBOARD_TOTP_SECRET=' + s); print(totp.provisioning_uri(s))"

fake-update: ## simulate a Telegram message, e.g. make fake-update TEXT="/start 3"
	$(PY) scripts/fake_update.py "$(TEXT)"

dry-run-log: ## show messages the bot would have sent (dry-run mode)
	@tail -n 20 var/telegram_dry_run.jsonl 2>/dev/null || echo "no dry-run messages yet"

llm-ping: ## one structured call through the LLM client (needs OPENAI_API_KEY)
	$(PY) scripts/llm_ping.py

dispatcher-pause: ## pause outbox delivery (restore procedure)
	$(PY) scripts/dispatcher_ctl.py pause

dispatcher-resume: ## resume outbox delivery
	$(PY) scripts/dispatcher_ctl.py resume

dispatcher-status: ## show dispatcher state and outbox backlog
	$(PY) scripts/dispatcher_ctl.py status

eval-offline: ## credential-free citation, tailoring, guardrail and prompt-boundary checks
	$(PY) -m evals.runners.prep
	$(PY) -m evals.runners.tailoring
	$(PY) -m evals.runners.guardrails
	$(PY) -m evals.runners.job_chat

eval-prep-live: ## held-out prep model quality gate (needs OpenAI key; makes paid calls)
	$(PY) -m evals.runners.prep --live
