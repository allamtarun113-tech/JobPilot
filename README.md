# JobPilot

**A personal AI job-hunting co-pilot that lives in Telegram.**

Send `/start` to your bot and JobPilot finds new jobs that fit your profile, scores each one, and sends you a
**job card** for every job that clears your bar. Each card has the company, the role, a match score with the
reasons, the skills you'd need to reach 100% (with free learning links), a resume tailored to that job, and the
apply link. It can also draft a short, professional email to the hiring team.

**You stay in control.** JobPilot never applies for you, never logs into a job board, and never sends a resume
or an email without your tap on the exact version.

---

## Contents

- [What it does](#what-it-does)
- [How a run works](#how-a-run-works)
- [What makes the resume trustworthy](#what-makes-the-resume-trustworthy)
- [Tech stack](#tech-stack)
- [Run it locally](#run-it-locally)
  - [1. Install the tools](#1-install-the-tools)
  - [2. Get the code and install packages](#2-get-the-code-and-install-packages)
  - [3. Fill in `.env`](#3-fill-in-env)
  - [4. Create the databases](#4-create-the-databases)
  - [5. Prepare the PDF builder (Tectonic)](#5-prepare-the-pdf-builder-tectonic)
  - [6. Add your resume](#6-add-your-resume)
  - [7. Start JobPilot](#7-start-jobpilot)
  - [8. Try a run without Telegram (dry run)](#8-try-a-run-without-telegram-dry-run)
  - [9. Connect a real Telegram bot](#9-connect-a-real-telegram-bot)
  - [10. Optional extras](#10-optional-extras)
- [Using JobPilot](#using-jobpilot)
- [Everyday commands](#everyday-commands)
- [Configuration](#configuration)
- [Running costs](#running-costs)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Privacy and safety](#privacy-and-safety)
- [More documentation](#more-documentation)

---

## What it does

| Feature | What you get |
|---|---|
| **Job search** | New jobs since your last check, from an AI web search across LinkedIn, Lever, Ashby, Greenhouse and company career pages, plus a jobs API (Google for Jobs via JSearch), plus any job link you share with the bot. Duplicates are merged. |
| **Match score (0-100)** | One AI call reads the job's requirements; **code** computes the score against your confirmed profile, so the same job and profile always give the same score. |
| **Skills to learn** | The missing skills, each with the points it would add, ranked by how quick it is to learn, with free courses, videos and docs from a curated catalog (the AI never invents links). |
| **Tailored resume** | Built for each job from your confirmed profile only: most relevant experience and projects first, the job's required skills first, spelling and formatting fixed, one or two pages. |
| **HR email** | A short professional email: greeting, why you fit (one experience, one project, your matching skills), closing and your contact details. Shown to you before anything is sent. |
| **Job Chat** | Reply to a card in Telegram to ask about the job, request a resume change, or ask for interview help. |
| **Interview prep** | A Prepare pack per job: skill recaps, likely questions with answers, and the sources behind them. |
| **Dashboard** | A web page with every card, its score, resume, email, chat history and status. |

## How a run works

```
/start
  │
  ├─ 1. Find      web search + jobs API + shared links  →  new jobs since last time
  ├─ 2. Filter    duplicates, your cities, titles to avoid, blacklist           (code)
  ├─ 3. Score     AI extracts the requirements → code computes the 0-100 match
  ├─ 4. Card      for jobs at or above your bar:
  │                 · resume tailored and checked line by line          (AI + code)
  │                 · email draft written and checked                   (AI + code)
  │                 · card sent to Telegram with the resume PDF
  └─ 5. You       Approve resume vN → apply yourself → (optional) Email HR → Send vN
```

A run stops on its own when it reaches your daily card limit or runs out of jobs; `/stop` ends it cleanly at
any time. Jobs it didn't get to wait in a queue for the next `/start`.

## What makes the resume trustworthy

The resume is assembled by **code** from the facts you confirmed on your profile. The AI only chooses the order
and suggests rewordings, and code checks every suggestion:

- **Nothing is invented.** A reworded line may only use the words of the fact it came from (plus word forms,
  spelling fixes and small joining words). New results, metrics, tools, titles or claims are refused, and the
  original wording is kept.
- **What you did stays true.** "Learned about" never becomes "designed"; planned work stays planned.
- **Nothing is removed.** Every job, project, detail and activity stays; new projects and details from your
  profile are added. The skills section lists only your skills that the job asks for, required ones first.
- **Clean output.** Spelling, capitals, dates and full stops are fixed by code (offline dictionary), each bullet
  is at most two lines, and the PDF is built from one fixed LaTeX template in a no-network sandbox.

## Tech stack

| Part | Built with |
|---|---|
| Language and packages | Python 3.12, [uv](https://docs.astral.sh/uv/) |
| Run pipeline | LangGraph (fixed step order, Postgres checkpoints) |
| AI | OpenAI `gpt-4o-mini` (structured outputs) and OpenAI web search |
| Database, queue, outbox | PostgreSQL (local, or Supabase in the cloud) |
| Vectors | Qdrant (embedded locally, or Qdrant Cloud) with a local embedding model (fastembed, BAAI/bge-small) |
| Dashboard | FastAPI + Jinja + HTMX |
| Telegram, Gmail, web search | MCP servers (official MCP Python SDK), run as subprocesses |
| Resume PDFs | Tectonic (LaTeX) in a sandbox, previews with pypdfium2 |
| Tests and checks | pytest, ruff, mypy, Alembic |

---

## Run it locally

These steps take a fresh machine to a working JobPilot. The quickest path (steps 1-8) needs **only an OpenAI
API key**: Telegram runs in dry-run mode, the database and vectors are local, and no email is ever sent.

> **Platform:** macOS is the main platform (resume PDFs build inside macOS `sandbox-exec`). Linux works too, with
> Docker for the PDF sandbox (see step 5). On Windows, use WSL2 and follow the Linux steps.

### 1. Install the tools

| Tool | Why | macOS (Homebrew) |
|---|---|---|
| Git | get the code | `brew install git` |
| uv | Python 3.12 and all packages | `brew install uv` |
| PostgreSQL 15+ | the database | `brew install postgresql@16 && brew services start postgresql@16` |
| Tectonic | builds resume PDFs from LaTeX | `brew install tectonic` |
| make | runs the project commands | included with Xcode Command Line Tools: `xcode-select --install` |

On Linux (Debian/Ubuntu): install `git`, `make`, `postgresql`, Docker, and uv
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). Make sure your user can create databases
(`sudo -u postgres createuser --superuser "$USER"`).

Check that everything is there:

```bash
git --version && uv --version && psql --version && tectonic --version && make --version
```

### 2. Get the code and install packages

```bash
git clone <repository-url> JobPilot
cd JobPilot
uv sync --all-groups          # downloads Python 3.12 if needed and installs every package into .venv
```

### 3. Fill in `.env`

```bash
cp .env.example .env
```

Open `.env` in an editor. For a first local run you only need:

| Name | Value |
|---|---|
| `DATABASE_URL` | keep `postgresql+psycopg://localhost/jobpilot` (your local database) |
| `OPENAI_API_KEY` | your key from [platform.openai.com](https://platform.openai.com) → **API keys** → **Create new secret key** |

Leave everything else empty for now:

- `TELEGRAM_BOT_TOKEN` empty → **dry-run mode** (messages go to a file instead of Telegram).
- `QDRANT_URL` empty → vectors are stored locally in `var/qdrant`.
- `SUPABASE_URL` empty → resume PDFs are stored locally in `Data/user/storage`.
- The `MCP_*_KEY` values may stay empty in development (fixed development keys are used). For anything beyond
  local testing, fill them and `APPROVAL_TOKEN_KEY` with long random strings:
  `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.

> `.env` holds secrets. It is git-ignored; never commit it.

### 4. Create the databases

```bash
make setup
```

This installs packages, creates two local databases (`jobpilot` for use, `jobpilot_test` for the tests) and
applies all migrations. It is safe to run again.

> `make migrate` refuses to touch a non-local database unless you add `CONFIRM=yes`, so a cloud database is
> never changed by accident.

### 5. Prepare the PDF builder (Tectonic)

Resume PDFs are compiled with no network access, so Tectonic's packages must be downloaded once beforehand.

**macOS** — compile the warm-up file once (this is the only step that downloads anything):

```bash
tectonic -o /tmp deploy/sandbox/warm.tex
```

**Linux** — build the sandbox image once (JobPilot runs Tectonic inside it with `--network none`):

```bash
docker build -t jobpilot-tectonic deploy/sandbox
```

### 6. Add your resume

JobPilot reads your resume from a LaTeX file and turns it into structured data plus *draft* profile items.

```bash
mkdir -p Data/user
cp /path/to/your/resume.tex Data/user/resume.tex
make import-resume
```

No resume at hand? Start with the sample: `cp tests/fixtures/sample_resume.tex Data/user/resume.tex`.

> The importer understands the layout of `templates/resume/resume.tex.j2` (sections with `\resumesection`,
> dated rows with `\datedrow`, skills with `\skill`, and `itemize` bullets). A resume in a different LaTeX layout
> needs to be adapted to these macros first. `Data/user/` is git-ignored, so your personal files stay private.

### 7. Start JobPilot

```bash
make dev
```

This starts all three processes (logs in `var/`):

| Process | What it does |
|---|---|
| `http` | the dashboard at **http://127.0.0.1:8765** |
| `worker` | Telegram polling, the work queue and runs |
| `dispatcher` | delivers queued Telegram messages and emails |

Then, in the browser:

1. Open **http://127.0.0.1:8765/profile** and **confirm** your profile items (only confirmed facts are ever
   used on a resume or in an email). Add experiences, projects and details there too.
2. Open **Preferences** and set your target titles, cities, match bar and cards per day.

> Locally the dashboard needs no login because it only listens on your own machine. Login (TOTP) is required
> as soon as it is reachable from elsewhere — see [Configuration](#configuration).

### 8. Try a run without Telegram (dry run)

With `TELEGRAM_BOT_TOKEN` empty, simulate a message from you and read what the bot would have sent:

```bash
make fake-update TEXT="/start 3"     # ask for up to 3 cards
make dry-run-log                     # the bot's replies, cards included
```

Cards, resumes and scores also appear on the dashboard at http://127.0.0.1:8765. A full run takes a few minutes
(searching, then scoring job by job).

### 9. Connect a real Telegram bot

1. In Telegram, open **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
   BotFather replies with a token like `123456789:AA...`.
2. Put it in `.env`: `TELEGRAM_BOT_TOKEN=123456789:AA...`
3. Send any message (for example `hi`) to your new bot from your own Telegram account.
4. Find your chat id: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the
   number at `"chat":{"id": ...}`. Put it in `.env`: `TELEGRAM_CHAT_ID=...`
   (the bot answers **only** this chat).
5. Stop `make dev` (Ctrl+C) and start it again. Send `/start 3` to your bot.

### 10. Optional extras

| Want | Do |
|---|---|
| More jobs from Google for Jobs | Sign up for **JSearch** on [RapidAPI](https://rapidapi.com) (free plan: 200 requests/month), copy the key into `JSEARCH_API_KEY` |
| HR emails through your Gmail | In Google Cloud Console: create a project → **APIs & Services** → enable **Gmail API** → **OAuth consent screen** (External, publish to production so tokens don't expire after 7 days) → **Credentials** → **Create credentials** → **OAuth client ID** → *Desktop app*. Put the client id and secret in `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`, run `make gmail-token-key` and paste the result into `GMAIL_TOKEN_KEY`, then run `make gmail-auth` once. |
| Interview-prep book corpus | Put the PDFs listed in `Data/books/books.json` in `Data/books/pdfs/`, then `make ingest-corpus` (and `make ingest-learning` for the learning catalog) |
| Tracing of every AI call | A free [Langfuse Cloud](https://cloud.langfuse.com) project: fill `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| Keep the backend running on a Mac | `make autostart-install` (launchd starts it at login); restart after changes with `launchctl kickstart -k gui/$(id -u)/com.jobpilot.backend` |
| Cloud setup (Supabase, Qdrant Cloud, Vercel dashboard) | Follow [`docs/deployment.md`](docs/deployment.md) |

> **Emails are never sent while `shadow_mode: true`** in `config/defaults.yaml` (the default). They are logged
> instead. Turn it off only when you have checked several drafts.

---

## Using JobPilot

### Telegram commands

| Command | What it does |
|---|---|
| `/start [n]` | Find and card jobs since the last check (optionally at most `n` cards) |
| `/status` | Current step, jobs seen / filtered / carded, queue size |
| `/pause` · `/resume` | Hold after the current job; continue in the same run |
| `/stop` | Finish the current job, save progress, send a summary |
| `/digest` | Jobs below your bar, queued jobs, cards with no action yet |
| `/prefs` | View or change preferences (titles, cities, bar, limits) |
| `/chat` or reply to a card | Job Chat about that job |
| Share a job link | It becomes a card like any other |

### Card buttons

**Approve resume vN** · **Apply** (opens the job page) · **View resume** · **More…** (*I applied*,
*Email HR*, *Prepare*, *Skip*).
Approving names an exact resume version; if the resume changed in between, the tap is refused and the new
version is shown. Email HR works after you approve the resume and mark the job applied; the email preview has
**Send vN / Edit / Skip**.

---

## Everyday commands

```bash
make help             # every command with a one-line description
make dev              # dashboard + worker + dispatcher (local development)
make backend          # worker + dispatcher only (when the dashboard runs elsewhere)
make check            # lint + type check + migration drift + all tests
make eval-offline     # safety and quality checks that need no API key
make resume-trial     # try resume tailoring on scored jobs, no cards or messages (PDFs in Data/user/trials)
make rescore          # re-score queued jobs with the current rules (no run active)
make backup           # encrypted database backup into Data/user/backups/ (needs BACKUP_KEY)
```

## Configuration

| Where | What |
|---|---|
| `.env` | Secrets and connection strings (names and help in `.env.example`) |
| `config/defaults.yaml` | Tunables: AI model per task, prompt versions, search budget per run, resume page limit and bullet length, shadow mode, caps |
| `config/scoring.yaml` | Match-score weights (bump `rubric_version` after a change, then `make rescore`) |
| Dashboard → Preferences (or `/prefs`) | Your target titles, cities, remote, blacklist, match bar, cards per day, digest mode |
| `prompts/<area>/<name>_vN.md` | The AI instructions; a changed prompt is a new file and its version is pinned in `config/defaults.yaml` |

**Dashboard login.** When the dashboard is reachable beyond your own machine, set `DASHBOARD_TOTP_SECRET`
(`make totp-secret`, then scan the printed URI with an authenticator app) and a fixed
`DASHBOARD_SESSION_SECRET`.

## Running costs

JobPilot is asleep until you send `/start`, so nothing is spent in between.

| Item | Rough cost |
|---|---|
| AI calls (`gpt-4o-mini`) | a few cents per `/start` |
| OpenAI web search | about **$0.01 per search**; a `/start` runs up to 48 lead searches plus description lookups. Lower `agents.job_finder.lead_searches_per_run` and `max_jd_lookups_per_run` in `config/defaults.yaml` to spend less |
| JSearch | free plan: 200 requests a month (about 6 a day) |
| Everything else locally | free |

## Project structure

```
JobPilot/
├── src/jobpilot/
│   ├── web/           dashboard (FastAPI + HTMX)
│   ├── worker/        Telegram polling, work queue, scheduler
│   ├── dispatcher/    delivers queued messages and emails (only part that can send)
│   ├── servers/       MCP servers: telegram/, gmail/, web/
│   ├── run/           the /start pipeline and its steps
│   ├── sources/       job sources, de-duplication, queue ranking
│   ├── matching/      filters, scoring, skills to learn
│   ├── resume/        pool, composer, polish, render, verifier, reviewer
│   ├── hr_email/      address finding, email writer, drafts, sending
│   ├── cards/         job cards and their states
│   ├── bot/           Telegram commands and buttons
│   ├── chat/          Job Chat
│   ├── prep/          interview preparation
│   ├── profile/       profile items and preferences
│   ├── db/            tables, queries, migrations
│   ├── safety/        fact checks, email checks, tokens, sanitizing
│   ├── llm/           the one model client, prompts, embeddings
│   └── core/ models/ queue/ integrations/ monitoring/ reference/ knowledge/
├── config/            tunables (YAML, no code)
├── prompts/           AI instructions, versioned
├── templates/         the resume LaTeX template and dashboard pages
├── deploy/            process manager configs, launchd, Docker, PDF sandbox
├── scripts/           one-off tools (import resume, backup, reconcile, ...)
├── evals/             quality and safety evaluations
├── tests/             unit, integration, concurrency, contract, end-to-end
├── docs/              decisions, runbook, deployment, threat model
└── Data/              reference data (skills taxonomy, learning catalog, book manifest)
    └── user/          your private files (git-ignored)
```

## Testing

```bash
make check            # ruff + mypy + Alembic drift check + pytest (uses the local jobpilot_test database)
make test             # tests only
make eval-offline     # credential-free safety checks: fact citations, tailoring, guardrails, prompt boundaries
```

The tests never call OpenAI: the model is replaced by scripted fakes, including replays of real mistakes the
resume checks must refuse.

## Troubleshooting

| Problem | Fix |
|---|---|
| `tectonic is not installed` | Install it (`brew install tectonic`), then run the warm-up in [step 5](#5-prepare-the-pdf-builder-tectonic) |
| PDF build fails offline / missing package | Run the step 5 warm-up once with internet access |
| `connection refused` to Postgres | Start it: `brew services start postgresql@16` (Linux: `sudo systemctl start postgresql`) |
| `database "jobpilot" does not exist` | `make setup` (or `make db-create migrate`) |
| Qdrant "already accessed by another instance" | Local Qdrant allows one process; stop any second `make dev`/`make worker` |
| No cards after a run | Jobs scored below your bar: lower the bar in Preferences, add target titles, or confirm more profile items |
| "daily jobs-API quota is used up" | Normal on the free JSearch plan; job descriptions then come from web search |
| Telegram bot doesn't answer | Check `TELEGRAM_CHAT_ID` is your chat, restart `make dev`, look in `var/worker.log` |
| First run is slow | The embedding model (~130 MB) downloads once into `var/models` |

Logs: `var/worker.log`, `var/dispatcher.log`, `var/http.log`.

## Privacy and safety

- **Your data stays yours.** `.env`, `var/` and `Data/user/` (resume, profile exports, PDFs, backups) are
  git-ignored. OpenAI requests are sent with storage disabled.
- **No scraping by JobPilot's own fetcher** of LinkedIn, Naukri or Indeed pages; their jobs come through search
  results and the jobs API.
- **Nothing is sent without you.** Every resume and email needs your tap on the exact version; emails go out
  exactly once per job, and only when shadow mode is off.
- **Untrusted text is data.** Job descriptions and web pages are sanitized and fenced; instructions inside them
  are ignored.
- **The bot answers one chat only** — yours.

## More documentation

| Document | What's in it |
|---|---|
| [`docs/how-it-works.md`](docs/how-it-works.md) | A walkthrough of the whole system |
| [`docs/JobPilot_Complete_Guide.md`](docs/JobPilot_Complete_Guide.md) | Every feature, which part is AI and which is code |
| [`docs/deployment.md`](docs/deployment.md) | Cloud setup: Supabase, Qdrant Cloud, Vercel |
| [`docs/runbook.md`](docs/runbook.md) | Day-to-day operation and alerts |
| [`docs/decisions.md`](docs/decisions.md) | Why the code is the way it is (numbered decisions) |
| [`docs/threat_model.md`](docs/threat_model.md) · [`docs/processors.md`](docs/processors.md) | Security model and data processors |
| [`Data/README.md`](Data/README.md) | The reference data and how it is used |
