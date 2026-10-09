# JobPilot: The Complete Guide

What JobPilot is, which parts are AI and which are plain code, how to build it yourself, and where the money goes.

*Written 2026-10-07 against the code in `src/jobpilot/` (phases P0-P7 built, P8 partly done). All file paths below
are inside `src/jobpilot/` unless they start with a top-level folder such as `config/`, `prompts/` or `Data/`.*

> Related docs: `docs/how-it-works.md` (a plain-English walkthrough of one `/start`), `docs/decisions.md` (why each
> choice was made), `CLAUDE.md` (the rules for working in this repo).

---

## Contents

- **Part 1: The project explained**
  - 1.1 What JobPilot does, in one minute
  - 1.2 The big picture (architecture)
  - 1.3 The golden rule: AI suggests, code decides
  - 1.4 Every AI call in the project (the complete list)
  - 1.5 The four agents
  - 1.6 The full journey, step by step, marked AI or code
  - 1.7 The safety machinery (all code)
  - 1.8 Where data lives
  - 1.9 Folder-by-folder map
  - 1.10 Frameworks and libraries: what each file uses
- **Part 2: Build it yourself (a tutorial)**
  - 2.0 What to learn first
  - Steps 1-19, from an empty folder to going live
- **Part 3: What costs money and how to spend less**
  - 3.1 Every cost in the system
  - 3.2 Real numbers from your database
  - 3.3 The most expensive parts, ranked
  - 3.4 How to cut costs (with the exact setting or file)
  - 3.5 A cheaper configuration you can copy

---

# Part 1: The project explained

## 1.1 What JobPilot does, in one minute

JobPilot is a personal job-hunting assistant for one person (you). You talk to it on **Telegram**.

1. You send `/start`.
2. It collects every job posted since your last check (from web searches, a jobs API, and links you share).
3. It removes duplicates and jobs you said you never want.
4. It scores each job against your profile (0-100).
5. For every job that clears your bar, it writes a **resume tailored to that job** and sends you a **job card**: company,
   role, match score, why you fit, the skills you'd need to learn for 100% (with free learning links), a preview of
   the tailored resume, and the apply link.
6. You tap buttons: **Approve resume**, **Apply** (you apply yourself, always), **Email HR**, **Prepare** (an interview
   prep pack), or **Skip**.
7. A private website (the **dashboard**) shows everything in more detail.

Three promises run through the whole design:

- **You apply, always.** No code fills forms or logs in to job boards.
- **Nothing speaks for you without approval.** A resume or email is used only after you tap Approve or Send on
  that exact version.
- **Never invent.** The AI may reword and reorder your real facts. It may not add a skill, title or number you
  didn't give it. Code checks this.

---

## 1.2 The big picture (architecture)

```
   You (phone)                                      You (browser)
       │  Telegram                                        │  HTTPS + 6-digit TOTP login
       ▼                                                  ▼
 ┌──────────────────────── YOUR LAPTOP (launchd keeps it running) ──────┐   ┌──── VERCEL ─────┐
 │                                                                       │   │  Dashboard      │
 │  WORKER  (worker/main.py)                 DISPATCHER (dispatcher/)    │   │  web/main.py    │
 │   • reads Telegram (long polling)          • the ONLY sender          │   │  FastAPI + HTMX │
 │   • runs /start (LangGraph batch)          • reads the outbox table   │   │  (read + record │
 │   • scores jobs, builds cards              • Telegram send/edit/photo │   │   actions as    │
 │   • Job Chat, Prepare, HR drafts           • Gmail draft / send       │   │   events)       │
 │   • read-only MCP: web search + fetch      • send-capable MCP servers │   └────────┬────────┘
 └──────────┬───────────────────────────────────────────┬───────────────┘            │
            │                                           │                            │
            ▼                                           ▼                            ▼
   ┌──────────────────────────────── SUPABASE (Postgres, Mumbai) ─────────────────────────────┐
   │ events · work_items (queue) · runs · jobs · scores · cards · resume_versions · emails ·  │
   │ approvals · outbox · sent_emails · profile_items · preferences · llm_usage · ...         │
   │ + a private Storage bucket for resume PDFs                                               │
   └──────────────────────────────────────────────────────────────────────────────────────────┘
            │
            ▼
   QDRANT CLOUD (vectors): prep_chunks · learning_resources · skill_notes · qa_bank · profile/job vectors
   OPENAI API: gpt-4o-mini for every AI call + the web_search tool
   JSEARCH (RapidAPI): Google for Jobs results, 6 calls/day on the free plan
   LANGFUSE CLOUD: traces of every run, AI call and tool call
```

**Why three processes?** Each has one job and a different permission level:

| Process | Runs where | Can it send messages/emails? | Entry point |
|---|---|---|---|
| **worker** | laptop | **No.** It only *writes rows* saying "please send this" | `worker/main.py` |
| **dispatcher** | laptop | **Yes**, the only one that can | `dispatcher/main.py` |
| **dashboard (http)** | Vercel | No. Your clicks become events, like taps | `web/main.py` (Vercel entry: `app.py`) |

They never call each other. They talk **through the database**: one writes a row, another reads it. The dispatcher
is woken instantly with Postgres `LISTEN/NOTIFY`.

**What is MCP here?** MCP (Model Context Protocol) servers are small helper programs started as subprocesses. Each
one exposes a short, fixed list of tools and refuses anything else:

| MCP server | Folder | Tools | Who connects |
|---|---|---|---|
| Telegram | `servers/telegram/` | `send_message`, `edit_message`, `send_document`, `send_photo`, `answer_callback` | dispatcher (poller may only `answer_callback`) |
| Gmail | `servers/gmail/` | `create_draft`, `find_sent`, `send_draft` (needs a signed token) | dispatcher only |
| Web | `servers/web/` | `search` (OpenAI web search), `fetch` (with SSRF guard + deny-list) | worker (read-only) |

---

## 1.3 The golden rule: AI suggests, code decides

Most of JobPilot is **plain code**. AI is used only where a human-like reading or writing step is needed, and every AI
answer comes back as a **typed Pydantic object** that code then checks before using.

Legend used in the rest of this document:

| Mark | Meaning | Costs money? |
|---|---|---|
| 🤖 **AI** | A call to OpenAI `gpt-4o-mini` through `llm/client.py` | Yes (tokens) |
| 🕵️ **Agent** | AI in a loop that chooses tools (search / fetch), capped by `llm/agent_runtime.py` | Yes (tokens + web searches) |
| 🔎 **Web search** | OpenAI's `web_search` tool, called by `servers/web/search.py` | Yes (~$0.01 per search + tokens) |
| 🧮 **Local model** | Text embeddings with `BAAI/bge-small-en-v1.5` running on your laptop (`llm/embeddings.py`) | No |
| ⚙️ **Code** | Plain Python: rules, math, database, checks | No |

Examples of the split:

| Decision | Who decides |
|---|---|
| "What does this JD ask for?" | 🤖 AI reads it (`matching/extract.py`) |
| "What is the match score?" | ⚙️ Code computes it from the AI's list (`matching/compute.py`) |
| "How should this bullet be reworded?" | 🤖 AI picks, orders and rewords bullets by id; code checks each against its fact (`resume/compose.py`, `safety/fidelity.py`) |
| "Is this edit allowed?" | ⚙️ Code checks every number, skill and LaTeX command (`resume/tex_editor.py` → `apply()`, `safety/claims.py`) |
| "Which page is this job's full JD?" | 🕵️ Agent picks a page (`sources/finder_agent.py` → `find()`) |
| "Is that really the same job?" | ⚙️ Code checks company + title match |
| "Which HR address to use?" | 🕵️ Agent reports addresses it *saw*; ⚙️ code keeps only ones on the company's own domain |
| "Send the email?" | 👤 **You**, by tapping Send vN. Then ⚙️ code mints a token, the Gmail server verifies it |

---

## 1.4 Every AI call in the project (the complete list)

All AI calls go through **one class**, `LLMClient.structured()` in `llm/client.py`. It:

1. picks the pinned model for the task from `config/defaults.yaml` → `models:`;
2. loads the pinned prompt from `prompts/<area>/<name>_v<N>.md` (version in `prompt_versions:`);
3. asks OpenAI for **strict JSON** matching a Pydantic schema;
4. retries **once** if the output is invalid, then fails visibly;
5. records tokens and cost in the `llm_usage` table and adds them to the run's totals;
6. sends `store: False`, so OpenAI doesn't keep your profile.

This is the full list (the `task` name is what appears in `llm_usage.task`):

| # | Task | Kind | What it does | Code | Prompt | When it runs | Calls |
|---|---|---|---|---|---|---|---|
| 1 | `profile_extract` | 🤖 one call | Turns your resume + notes into draft profile facts (skills, projects, roles, metrics) | `profile/parser.py` | `prompts/profile/extract_items_v2.md` | When you import a resume | 1 per import |
| 2 | `role_expansion` | 🤖 one call | Suggests ~15 related job titles ("AI Engineer" → "ML Engineer", "LLM Engineer"...) | `sources/role_expander.py` | `prompts/sources/role_expansion_v1.md` | Weekly, or when target titles change | ~1/week |
| 3 | `job_finder` (leads) | 🕵️ agent | Reads search results and lists single job postings | `sources/finder_agent.py` → `find_leads()` | `prompts/agents/job_finder_v3.md` | Every `/start` | 6 batches × 1-8 steps |
| 4 | `job_finder` (full JD) | 🕵️ agent | Searches and fetches pages to find a job's full description | `sources/finder_agent.py` → `find()` | same | Only when the free lookups fail | ≤ 60 jobs/run |
| 5 | `job_finder` (HR email) | 🕵️ agent | Finds the company's published hiring email | `sources/finder_agent.py` → `find_contacts()` | same | For every card (automatic HR draft) | 1 task per card |
| 6 | `extract_requirements` | 🤖 one call | Reads a JD → technologies, skills, concepts, project types, nice-to-haves, domain, years | `matching/extract.py` | `prompts/scoring/extract_requirements_v2.md` | Once per unique JD (cached) | 1 per new JD |
| 7 | `tex_editor` | 🤖 one call per round | Proposes find→replace edits to the job's copy of your resume LaTeX | `resume/tex_editor.py` → `propose()` | `prompts/resume/tex_edit_v1.md` | Every card, inside the tailoring loop | up to 6 per card |
| 8 | `resume_review` | 🤖 one call per round ("Resume Reviewer agent") | Scores the built PDF's text 0-100 like a recruiter, lists fixes | `resume/review.py` → `review()` | `prompts/resume/review_v2.md` | After each round that passes the verifier | up to 6 per card |
| 9 | `email_writer` | 🤖 one call (+1 retry) | Writes the HR email explaining your fit | `hr_email/email_writer.py` → `write()` | `prompts/hr_email/email_writer_v3.md` | Automatically for every card; again on Edit | 1-2 per card |
| 10 | `job_chat` | 🤖 one decision per message ("Job Chat agent") | Picks a tool (answer, edit resume, edit email, explain score, prep help, or a proposal) | `chat/service.py` | `prompts/agents/job_chat_v2.md` | When you reply to a card or `/chat` | 1 per message (+ an edit round) |
| 11 | `prep_research` | 🕵️ agent | Reads public interview-experience pages for the company and role | `prep/research.py` | `prompts/agents/prep_research_v2.md` | When you tap Prepare | ≤ 8 steps, ≤ 10 searches |
| 12 | `skill_note` | 🤖 one call | Writes a simple-English study note for one skill, from the books | `prep/skill_notes.py` → `write()` | `prompts/prep/skill_note_v1.md` | Once per skill, ever (`make skill-notes` or ≤ 4 per Prepare) | once per skill |
| 13 | `qa_generator` | 🤖 one call | Writes only the interview Q&A groups the Q&A bank doesn't have yet | `prep/qa_generator.py` → `generate_missing()` | `prompts/prep/qa_generator_v3.md` | When you tap Prepare | 0-1 per Prepare |
| 14 | `health_ping` | 🤖 tiny call | Checks the API key works | `scripts/llm_ping.py` | `prompts/system/ping_v1.md` | `make llm-ping` | manual |
| — | web search | 🔎 | Runs one search through OpenAI's `web_search` tool | `servers/web/search.py` | fixed text in the file | Inside every agent task | see Part 3 |

**Not AI, even though it sounds like it:**

| Feature | What actually does it | File |
|---|---|---|
| Stage A "rough match" | 🧮 local embeddings + cosine similarity | `matching/embed_score.py` |
| The final match score | ⚙️ arithmetic over the AI's requirement list + 🧮 embeddings for partial credit | `matching/compute.py` |
| "Why you fit" text | ⚙️ code builds it from the score's matches | `matching/compute.py` → `explain()` |
| Skills to learn + points | ⚙️ code (points = share of a dimension) | `matching/gap.py` |
| Learnability of a skill | ⚙️ code + 🧮 embeddings over Qdrant | `matching/gap.py` → `learnability()` |
| Learning links | ⚙️ lookup in `Data/learning/` (the AI never writes links) | `matching/gap_resources.py` |
| Deduplication | ⚙️ code (board id → company+title+city → near-duplicate text) | `sources/dedup.py` |
| Free JD lookup (Lever/Greenhouse/Ashby APIs, jobs API) | ⚙️ code | `sources/enrich_lookup.py` |
| LinkedIn leads | ⚙️ code reads the search-result *title* with a regex (the page is never opened) | `sources/finder_agent.py` → `linkedin_lead()` |
| Building and checking the PDF | ⚙️ Tectonic in a no-network sandbox + text read-back | `resume/render.py`, `resume/verifier.py`, `resume/parse_check.py` |
| Resume preview picture | ⚙️ pypdfium2 renders page 1 | `resume/preview.py` |
| Email checks (word count, salary talk, urgency, claims) | ⚙️ code | `safety/email_checks.py` |
| Choosing the HR address | ⚙️ code | `hr_email/service.py` → `verified()`, `choose()` |
| DSA practice patterns | ⚙️ code mapping JD tags to patterns | `prep/dsa_picker.py` |
| Book retrieval for Prepare | 🧮 embeddings + Qdrant search | `prep/retrieve.py` |
| Skill lookup (note + resources + Q&A) | ⚙️ Qdrant payload filters, no AI | `knowledge/lookup.py` |
| Study plan | ⚙️ code | `prep/study_plan.py` |
| The run's step order | ⚙️ LangGraph with fixed edges (not an AI supervisor) | `run/graph.py` |

---

## 1.5 The four agents

An "agent" here is AI that runs in a **loop**, choosing its next action. JobPilot keeps agents to a minimum and puts
hard caps around them. The two tool-using agents share one bounded runtime:

**`llm/agent_runtime.py` → `AgentRuntime`** enforces, *before* every step:

- steps per task (`max_steps_per_task`, 8);
- web searches per run (`max_searches_per_run`);
- pages fetched per run (`max_pages_per_run`) and per lead (`max_pages_per_lead`);
- tokens per run (`max_tokens_per_run`);
- and asks for no more output tokens than the run has left.

When a cap is hit it raises `AgentLimit` / `StepLimit`, the agent stops, and the run continues without it. Every step
and tool call becomes a tracing span.

### Agent 1: Job Finder (🕵️, `sources/finder_agent.py`)

Three tasks, all with the same loop: *code runs some searches first → the model sees the results → the model says
`search`, `fetch` or `finish` → repeat until finish or a cap.*

| Task | Input | What the AI does | What code does around it |
|---|---|---|---|
| `find_leads` | Your titles × sites × cities × levels | Picks single postings out of search results | Builds the 48 queries and rotates them across runs (`lead_query_pool()`, `lead_queries()`); forces at least 4 searches; refuses page fetches; keeps a lead only if its URL really came back from search, is new, and is not a listing page (`is_listing()`); parses LinkedIn result titles itself |
| `find_full_jd` | One job's title, company, snippet | Searches, fetches up to 3 pages, picks the page that is this job | Allows fetching only URLs that came from its searches; accepts the pick only if the company and title match the lead and the page text names both; ≥ 200 characters |
| `find_hr_email` | A company name | Reports email addresses it saw, with the page | Runs 3 fixed searches first; keeps only addresses literally present in observed text; `hr_email/service.py` keeps only the company's own domain and prefers careers@/jobs@/hr@ |

**The agent never filters or scores jobs.** That is a design rule.

### Agent 2: Prep Research (🕵️, `prep/research.py`)

Runs when you tap Prepare. Searches and reads public interview-experience pages for the company and role, and
returns notes. Code keeps a note only if its source URL is a page the agent actually searched for and fetched.
Caps: 10 searches, 20 pages, 60k tokens per Prepare request.

### Agent 3: Job Chat (🤖, `chat/service.py`)

When you reply to a card, one AI call returns a `JobChatDecision` (`models/job_chat.py`): which tool to use and its
arguments. Code then executes it:

- **Tier 1** (makes a new draft version or answers): `answer`, `edit_resume` (one tailoring round via
  `chat/resume_edit.py`), `edit_email` (re-checked by `safety/email_checks.py`), `explain_score`, `prep_help`.
- **Tier 2** (only creates a **proposal** with Confirm/Cancel buttons): `update_profile`, `change_prefs`,
  `skip_snooze`. Nothing permanent changes until you tap Confirm (`chat/proposal_service.py`).

It can never send, apply or approve. The JD is placed in a fenced data block, so instructions hidden in a job post are
treated as text. Job Chat has its own token budget (`budgets.job_chat_tokens_per_day`).

### Agent 4: Resume Reviewer (🤖, `resume/review.py`)

Called an "agent" by decision 19, but technically **one AI call per round** inside a loop that **code** drives
(`resume/tailor.py`). It reads the text extracted from the built PDF, scores it 0-100 against the job, lists fixes the
editor can make with your facts, and separately lists what only *new facts from you* could fix (`needs_facts`).

The tailoring loop (all in `resume/tailor.py` → `tailor()`):

```
for round in 1..6:
    🤖 tex_editor.propose()        → find/replace edits on this job's .tex copy
    ⚙️ tex_editor.apply()          → refuse unsafe LaTeX, locked sections, unbacked claims
    ⚙️ verifier.verify()           → build PDF in sandbox; 1 page; name + sections in order; edits visible
       failed? → send problems back to the editor, try again
    🤖 review.review()             → score 0-100 + fixes
    ⚙️ keep the best-scoring copy
    stop if score ≥ 95, or 2 rounds in a row didn't improve, or editor has nothing left to change
```

---

## 1.6 The full journey, step by step, marked AI or code

For more words on each step, see `docs/how-it-works.md`. Here every step is labelled.

### A. You send `/start`

| Step | Who | What happens | Code |
|---|---|---|---|
| 1 | ⚙️ | The poller long-polls Telegram, saves each update in `events` (unique on `update_id`, so repeats are ignored) and queues a work item | `worker/telegram_poller.py`, `bot/router.py` → `ingest_update()` |
| 2 | ⚙️ | A worker loop claims work items with `SELECT … FOR UPDATE SKIP LOCKED` | `worker/queue_loop.py`, `db/queries/work_items.py` |
| 3 | ⚙️ | The router decides: command, button tap, shared link, or chat | `bot/router.py` → `handle_event()` |
| 4 | ⚙️ | `/start` takes the **run lease** (only one run at a time; a unique partial index on `runs`) | `bot/commands.py` → `start()`, `run/lease.py` |
| 5 | ⚙️ | The LangGraph run starts: `preflight → serve_queue → fetch → screen → process_job (loop) → drain → summary` | `run/graph.py` |

### B. The run finds jobs

| Step | Who | What happens | Code |
|---|---|---|---|
| 6 | ⚙️ | Preflight: kill switch off? profile confirmed? API quota left? | `run/steps/preflight.py` |
| 7 | ⚙️ | Serve jobs left in the queue from earlier runs, best first, with age decay; expire after 10 days | `run/steps/serve_queue.py`, `sources/queue_rank.py` |
| 8 | 🤖 (weekly) | Refresh related titles | `sources/role_expander.py`, `run/steps/fetch.py` → `refresh_related_titles()` |
| 9 | 🕵️ + 🔎 | Job Finder lead searches (48 per run, rotating) | `sources/job_finder.py`, `sources/finder_agent.py` → `find_leads()` |
| 10 | ⚙️ (paid API) | JSearch jobs API (6 calls/day) | `sources/jobs_api.py`, `integrations/jobs_api.py` |
| 11 | ⚙️ | Jobs you shared by link | `sources/shared_links.py`, `run/share.py` |
| 12 | ⚙️ | Each source's **cursor** moves forward only when its jobs are saved, in the same transaction | `sources/cursors.py` |
| 13 | ⚙️ | Deduplicate (board id → company+title+city → near-duplicate JD); repeats become "sightings" | `sources/dedup.py` |
| 14 | ⚙️ | Clean the text (strip injection tricks, fence it as data) | `safety/sanitize.py` |
| 15 | ⚙️ | Hard filters: blocked company, titles to avoid (location only changes the order) | `run/steps/screen.py`, `matching/hard_filters.py` |
| 16 | 🧮 | Stage A: embedding similarity profile ↔ job (orders jobs, removes nothing) | `matching/embed_score.py` → `stage_a()` |

### C. Each job, one at a time (`run/steps/process_job.py` → `run/pipeline.py` → `process()`)

| Step | Who | What happens | Code |
|---|---|---|---|
| 17 | ⚙️ then 🕵️ | Find the full JD: free careers-site APIs and the jobs API first; the agent only if those fail | `sources/jd_enrich.py` → `find_full_jd()`, `sources/enrich_lookup.py` |
| 18 | 🤖 (cached) | Extract requirements from the JD. Cache key = JD hash + prompt version + model id | `matching/extract.py` |
| 19 | ⚙️ + 🧮 | Compute the score: technologies 30, skills 20, concepts 10, domain 10, projects 20, nice-to-have 10 | `matching/compute.py`, weights in `config/scoring.yaml` |
| 20 | ⚙️ + 🧮 | Skills to learn, with points and learnability; learning links from the catalog | `matching/gap.py`, `matching/gap_resources.py` |
| 21 | ⚙️ | Above your bar? Queue it for a card. Below? Mark it, show it in `/digest` | `sources/queue_rank.py` → `enqueue()` |
| 22 | ⚙️ | After each job, check stop rules: card cap, `/stop`, budget, quota, error spike, queue empty | `run/stop_rules.py` → `evaluate()` |

### D. Build the card (`cards/build.py` → `build()`)

| Step | Who | What happens | Code |
|---|---|---|---|
| 23 | ⚙️ | Copy your base resume LaTeX for this job (original never edited) | `resume/tex_versions.py` |
| 24 | 🤖 + ⚙️ | Tailoring loop: editor → checks → build → verify → reviewer, up to 6 rounds, aim 95 | `resume/tailor.py` (see 1.5) |
| 25 | ⚙️ | Add missing job skills that are close to yours or quick to learn, if allowed | `resume/skill_additions.py` |
| 26 | ⚙️ | Save the PDF to Supabase Storage, render page 1 as a PNG | `integrations/storage.py`, `resume/preview.py` |
| 27 | ⚙️ | In **one transaction**: save `resume_versions` + `cards` + outbox rows (card text, photo, PDF) | `cards/build.py`, `queue/outbox.py` |
| 28 | ⚙️ | Queue a background HR-email draft for this card | `cards/build.py` (work item `email-auto:<job>:<version>`) |

### E. Delivery and your taps

| Step | Who | What happens | Code |
|---|---|---|---|
| 29 | ⚙️ | Dispatcher delivers outbox rows in order per card, exactly once, with retries and backoff | `dispatcher/delivery.py` → `deliver_ready()` |
| 30 | ⚙️ | Card text and buttons (callback data ≤ 64 bytes, short opaque ids) | `cards/render.py`, `cards/messages.py`, `core/ids.py` |
| 31 | ⚙️ | Tap → poller answers the spinner at once → event → handler | `worker/telegram_poller.py`, `bot/callbacks.py` |
| 32 | ⚙️ | Approve / I applied / Skip move the card's state machine, version-checked | `cards/transitions.py`, `cards/states.py` |
| 33 | ⚙️ | A stale button (older resume version) is refused and the newest is shown | `cards/approvals.py` |
| 34 | ⚙️ | Run summary to Telegram | `run/steps/summary.py` |

### F. HR email (exactly once)

| Step | Who | What happens | Code |
|---|---|---|---|
| 35 | ⚙️ then 🕵️ + 🔎 | Find the address: the JD → an address used before for this company → the Job Finder's hiring-email task | `hr_email/service.py` → `find_address()`, `sources/finder_agent.py` → `find_contacts()` |
| 36 | ⚙️ | Keep only addresses on the company's own domain; prefer careers@/jobs@/hr@ | `hr_email/service.py` → `verified()`, `choose()`, `hr_email/address_finder.py` |
| 37 | 🤖 | Write the email (≤ ~130 words), citing profile ids | `hr_email/email_writer.py` |
| 38 | ⚙️ | Check: claims cited, no salary talk, no false urgency, no placeholders, length; 1 retry with the problems | `safety/email_checks.py` |
| 39 | 👤 | You tap Email HR → preview with **Send vN / Edit / Skip** | `hr_email/drafts.py` |
| 40 | ⚙️ | Send vN: one transaction writes an `approvals` row, moves card → `sending`, writes an outbox row (no token) | `hr_email/send.py` |
| 41 | ⚙️ | Dispatcher mints a short-lived HMAC token from the approval; Gmail server verifies it before `send_draft` | `safety/approval_tokens.py`, `servers/gmail/token_verify.py` |
| 42 | ⚙️ | Before any retry: search Gmail Sent for this job's header first; `sent_emails.job_id` is UNIQUE | `dispatcher/delivery.py` → `_check_send()` |
| 43 | ⚙️ | Not sent in 24 h → `needs_reconfirm` | `queue/reconfirm.py` |
| — | ⚙️ | **Shadow mode is on**: the email is logged, not sent | `config/defaults.yaml` → `shadow_mode: true` |

### G. Prepare (interview pack, `prep/service.py`)

| Step | Who | What happens |
|---|---|---|
| 44 | ⚙️ | Skill recap from stored notes (`knowledge/lookup.py`) |
| 45 | 🤖 (rare) | Write up to 4 missing skill notes, saved forever (`prep/skill_notes.py`) |
| 46 | ⚙️ | Learning resources (no AI), ranked by points × learnability |
| 47 | 🧮 | Book sections for the topics (`prep/retrieve.py`, Qdrant `prep_chunks`) |
| 48 | 🕵️ + 🔎 | Prep Research agent: interview-experience pages (`prep/research.py`) |
| 49 | ⚙️ | Q&A from the bank first (`prep/qa_bank.py`) |
| 50 | 🤖 | One call writes only the missing Q&A groups (`prep/qa_generator.py`), then they're saved to the bank |
| 51 | ⚙️ | Every answer shows its source or "unverified" (`prep/qa_bank.py` → `group_source()`) |
| 52 | ⚙️ | DSA patterns (`prep/dsa_picker.py`), study plan (`prep/study_plan.py`), known/weak marks (`prep/marks.py`) |

### H. Background timers (`worker/scheduler.py`, all ⚙️)

Lease heartbeats, the daily reminder digest (`cards/reminders.py`), queue expiry, re-confirm sweep, redacting old
event payloads, and alerts (`monitoring/alerts.py`). **The scheduler never starts a run.**

---

## 1.7 The safety machinery (all code)

These are the parts that make JobPilot reliable. None of them use AI.

| Problem | Solution | Code |
|---|---|---|
| Telegram sends the same update twice | `events.idempotency_key` UNIQUE; `update_id` stored | `queue/events.py`, `safety/idempotency.py` |
| Two `/start` at once | Unique partial index on `runs` where status in (`running`, `draining`) + heartbeat; stale lease taken over | `run/lease.py`, `db/leases.py` |
| Worker crashes mid-run | LangGraph Postgres checkpointer; next `/start` resumes | `run/graph.py`, `scripts/setup_checkpointer.py` |
| Two writers change the same row | Optimistic locking: `UPDATE … WHERE id = x AND version = n`, conflict → retry | `db/queries/base.py` → `versioned_update()` |
| A message must be sent exactly when state changes | **Outbox**: the message row is written in the same transaction as the state change | `queue/outbox.py` |
| Messages arrive out of order | Outbox `partition_key` + `seq`; dispatcher sends each partition strictly in order | `dispatcher/delivery.py` |
| A long AI call holding a DB lock | Never: read → compute (no transaction) → short version-checked write | rule 10, everywhere |
| Approving something you didn't see | Buttons carry the exact version id; stale taps refused | `cards/approvals.py` |
| An email sent twice | Approval → token at delivery → Sent search before retry → `sent_emails.job_id` UNIQUE | `hr_email/send.py`, `dispatcher/delivery.py` |
| Prompt injection in a JD | Sanitize + fence as data; tier-2 changes need Confirm | `safety/sanitize.py`, `chat/policy.py` |
| AI inventing facts | Every number/skill/title must be in a confirmed profile fact | `safety/claims.py`, `safety/citations.py` |
| Dangerous LaTeX | Allow-listed commands only; no file reads, no shell, no new macros; no-network sandbox | `resume/tex_editor.py` → `latex_problem()`, `resume/render.py` |
| Fetching internal URLs (SSRF) | Resolve DNS first, refuse private/loopback ranges, re-check every redirect, connect to the checked IP | `servers/web/ssrf_guard.py`, `servers/web/fetch.py` |
| Scraping job boards | Deny-list: LinkedIn, Naukri, Indeed pages never fetched | `config/fetch_rules.yaml` |
| Runaway spend | Agent caps, daily caps, kill switch (`/killswitch on`) | `safety/caps.py`, `safety/killswitch.py` |
| Someone else using your bot | Poller checks the chat id; Telegram server refuses any other chat | `bot/auth.py`, `servers/telegram/auth.py` |
| Public dashboard | TOTP login, lockout after 5 failures, secure cookies, CSRF | `web/auth.py`, `web/login_guard.py`, `safety/totp.py` |

---

## 1.8 Where data lives

| Store | What | Code |
|---|---|---|
| **Postgres (Supabase)** | All state: events, work queue, runs, jobs, sightings, requirements, scores, cards, resume versions, emails, approvals, outbox, sent emails, chat, proposals, prep sets, usage ledgers | `db/tables.py`, migrations in `db/migrations/versions/0001…0014` |
| **Supabase Storage** (private bucket) | Resume PDFs and preview images | `integrations/storage.py` |
| **Qdrant Cloud** | `prep_chunks` (book sections), `learning_resources` (755 resources), `skill_notes`, `qa_bank`, profile/job vectors | `integrations/vectors.py` |
| **`Data/`** (read-only reference) | Skills taxonomy, learning resources, 32 prep books | `reference/catalog.py`, `knowledge/learning.py`, `prep/ingest.py` |
| **`Data/user/`** (private, git-ignored) | Your resume PDF and `.tex`, backups, local storage | `scripts/import_resume.py` |
| **`config/*.yaml`** | Tunables: caps, models, prompts, weights, fetch rules, MCP scopes | `core/config.py` |
| **`.env`** | Secrets (OpenAI, Telegram, Supabase, Gmail, Qdrant, TOTP) | `core/config.py` → `Settings` |

The card state machine (`cards/states.py`):

```
new ──approve──▶ resume_approved ──applied──▶ applied ──Send vN──▶ sending ──▶ emailed
 │                                                                    │
 └──skip──▶ skipped                                     24 h unsent ──▶ needs_reconfirm
 (any) ──▶ posting_closed      outcomes: interview / rejected / offer
```

---

## 1.9 Folder-by-folder map

Layers (enforced by `tests/unit/test_layers.py`): imports only point **down**.

```
entry points   web/  worker/  dispatcher/  servers/          ← start processes; nobody imports them
coordinators   run/  bot/  cards/  chat/                     ← combine features
features       sources/ matching/ resume/ hr_email/ profile/ prep/   ← never import each other
shared         db/ queue/ safety/ llm/ integrations/ monitoring/ reference/ knowledge/
base           core/  models/
```

| Folder | Responsibility | AI inside? |
|---|---|---|
| `core/` | Config loader, clock, errors, ids, logging, process pool | No |
| `models/` | Pydantic types: Job, Lead, Requirements, Score, ResumeDoc, EmailDraft… | No |
| `db/` | Tables, sessions, leases, all SQL (`queries/`), migrations | No |
| `queue/` | Events, work queue, outbox, re-confirm | No |
| `safety/` | Tokens, idempotency, TOTP, sanitize, claims, email checks, caps, kill switch | No |
| `llm/` | **The only AI gateway**: client, prompts, schema, pricing, agent runtime, embeddings | Yes (gateway) |
| `integrations/` | JSearch, Storage, Qdrant, MCP client/process, senders | No |
| `monitoring/` | Tracing, metrics, alerts, health, redaction | No |
| `reference/` | Skills taxonomy, name normalisation | No |
| `knowledge/` | Qdrant knowledge base (resources, lookup) | No (🧮 only) |
| `profile/` | Parse resume → items, confirm, preferences | 🤖 `parser.py` |
| `sources/` | Job sources, cursors, dedup, JD lookup, queue ranking | 🕵️ `finder_agent.py`, 🤖 `role_expander.py` |
| `matching/` | Filters, stage A, extraction, score, gap | 🤖 `extract.py` |
| `resume/` | LaTeX copy per job, editor, verifier, reviewer, render, preview | 🤖 `tex_editor.py`, `review.py` |
| `hr_email/` | Address, writer, drafts, send | 🤖 `email_writer.py` |
| `prep/` | Ingest books, retrieval, research, Q&A, notes, study plan | 🕵️ `research.py`, 🤖 `qa_generator.py`, `skill_notes.py` |
| `cards/` | State machine, build, render, reminders, rescore | No (calls resume/) |
| `chat/` | Job Chat | 🤖 `service.py` |
| `bot/` | Telegram router, commands, callbacks, message texts | No |
| `run/` | Lease, LangGraph graph, steps, stop rules, pipeline | No (calls features) |
| `servers/` | MCP servers: telegram, gmail, web | 🔎 `web/search.py` |
| `web/` | Dashboard routes | No |
| `worker/`, `dispatcher/` | Process main loops | No |

---

## 1.10 Frameworks and libraries: what each file uses

Python 3.12 is the language everywhere. Below, "stdlib" means only Python's built-in standard library (no framework).
The lists were taken from each file's actual `import` lines (2026-10-07).

### The stack at a glance

| Framework / library | What it is | What JobPilot uses it for | Main places |
|---|---|---|---|
| **FastAPI** (+ Starlette) | Web framework | The dashboard: routes, forms, cookies, middleware | `web/` |
| **Uvicorn** | ASGI web server | Runs the dashboard locally (`make http`); Vercel runs it in production | `web/main.py` |
| **Jinja2** | Template engine | Dashboard HTML pages, the prompt files, the LaTeX resume template | `templates/ui/`, `llm/prompts.py`, `resume/render.py` |
| **HTMX** 2.0.4 (JavaScript, from unpkg) | Small front-end library | Partial page updates on the dashboard without writing JavaScript | `templates/ui/base.html` |
| **SQLAlchemy 2** (Core, async) | Database toolkit | Table definitions and every SQL query (no ORM) | `db/`, and anything that takes a DB connection |
| **psycopg 3** (+ psycopg_pool) | Postgres driver | Connections to Supabase; `LISTEN/NOTIFY` for the dispatcher; the LangGraph checkpoint pool | `db/session.py`, `dispatcher/main.py`, `worker/main.py` |
| **Alembic** | Migration tool | Database migrations `0001`-`0014` | `db/migrations/` |
| **LangGraph** (+ langgraph-checkpoint-postgres) | Graph / workflow framework | The run's fixed step order and crash-resume checkpoints | `run/graph.py`, `worker/main.py` |
| **OpenAI Python SDK** | API client | Every AI call (Responses API, strict JSON output) | `llm/client.py` only |
| **httpx** | Async HTTP client | Telegram Bot API, Gmail API, JSearch, careers-site APIs, OpenAI web search, Supabase Storage, page fetches | `servers/`, `integrations/`, `sources/enrich_lookup.py`, `worker/telegram_poller.py` |
| **MCP Python SDK** (`mcp`) | Model Context Protocol | The three tool servers (Telegram, Gmail, Web) and the client that talks to them | `servers/*/server.py`, `integrations/mcp_client.py` |
| **anyio** | Async helpers | Starting the MCP servers as subprocesses | `integrations/mcp_process.py` |
| **Pydantic v2** | Data validation | Every data type and every AI output schema | `models/`, `llm/`, agents |
| **pydantic-settings** | Settings loader | Reading secrets from `.env` | `core/config.py` |
| **PyYAML** | YAML parser | Reading `config/*.yaml` | `core/config.py` |
| **fastembed** (ONNX Runtime) | Local embedding models | Text embeddings with `BAAI/bge-small-en-v1.5`, on the laptop | `llm/embeddings.py` |
| **qdrant-client** | Vector database client | The knowledge base and similarity search | `integrations/vectors.py` |
| **pypdf** | PDF reader | Reading text back from built resumes; reading the prep books | `resume/parse_check.py`, `prep/ingest.py` |
| **pypdfium2** (+ Pillow) | PDF renderer | Page-1 preview image of the resume | `resume/preview.py` |
| **Tectonic** (external program) | LaTeX compiler | Builds resume PDFs, inside `sandbox-exec` (macOS) or Docker, with no network | `resume/render.py`, `deploy/sandbox/tectonic.sb` |
| **cryptography** | Crypto library | Encrypting the stored Gmail token; approval-token signing keys | `safety/approval_tokens.py`, `servers/gmail/auth.py` |
| **itsdangerous** | Signed cookies | Dashboard session cookies (used through Starlette's session middleware) | `web/main.py` |
| **OpenTelemetry** (SDK + OTLP exporter) | Tracing | A span for every run, job, AI call, tool call and tap, sent to Langfuse Cloud | `monitoring/tracing.py` and many callers |
| **supervisor** | Process manager | Runs worker + dispatcher (`make backend`) or all three (`make dev`) | `deploy/supervisor*.conf` |
| **launchd** (macOS) | OS service manager | Starts the backend at login and keeps it running | `deploy/launchd/com.jobpilot.backend.plist` |
| **uv** | Package / environment manager | Installs dependencies, runs everything | `pyproject.toml`, `uv.lock`, `Makefile` |
| **pytest** + pytest-asyncio | Test framework | All tests | `tests/` |
| **ruff**, **mypy** | Linter/formatter, type checker | `make lint`, `make typecheck` | whole repo |
| **Vercel** (Python runtime) | Hosting | Serves the dashboard (`app.py` → `web.main:app`) | `app.py`, `vercel.json` |

Hosted services (no library of their own; reached over HTTP or Postgres): **Supabase** (Postgres + Storage REST API via
httpx), **Qdrant Cloud**, **OpenAI**, **Telegram Bot API**, **Gmail API**, **JSearch on RapidAPI**, **Langfuse Cloud**
(receives OpenTelemetry traces).

Standard-library modules worth knowing about, because they do important work: `asyncio` (everything is async),
`concurrent.futures` (the CPU process pool in `core/cpu.py`), `hmac`/`hashlib`/`base64` (TOTP, approval tokens, cache
keys), `ipaddress`/`socket` (the SSRF guard), `email` (building Gmail messages), `html` (escaping Telegram HTML and
parsing pages), `subprocess` (running Tectonic and PDF tools).

### File by file

All paths inside `src/jobpilot/`. Package `__init__.py` files are left out (they hold no logic).

**`core/`: basics**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `config.py` | Pydantic, pydantic-settings, PyYAML | Load `config/*.yaml` and `.env` into typed objects |
| `cpu.py` | stdlib `asyncio`, `concurrent.futures` | Process pool for CPU-heavy work |
| `clock.py`, `errors.py`, `ids.py`, `logging.py` | stdlib | Time (swappable in tests), error types, short ids, log setup |

**`models/`: data types**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `email.py`, `health.py`, `job.py`, `job_chat.py`, `preferences.py`, `prep.py`, `profile.py`, `requirements.py`, `resume_doc.py`, `resume_ops.py`, `resume_review.py`, `resume_tex.py`, `score.py` | Pydantic | Typed data and the AI output schemas |
| `card.py`, `run.py`, `work.py` | stdlib (`enum`) | Status enums |

**`db/`: database**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `tables.py` | SQLAlchemy Core | Every table, index and constraint |
| `session.py` | SQLAlchemy async + psycopg | Engine, `transaction()` and `read()` helpers |
| `leases.py` | SQLAlchemy | Short row leases (`locked_by`, `locked_until`) |
| `queries/*.py` (24 files: `base`, `cards`, `jobs`, `outbox`, `runs`, `work_items`, ...) | SQLAlchemy Core | All SQL; version-checked updates |
| `migrations/env.py` | Alembic, SQLAlchemy | Migration runner (filters out LangGraph's tables) |
| `migrations/versions/0001…0014` | Alembic | Schema changes |

**`queue/`: events, work queue, outbox**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `events.py`, `jobs_queue.py`, `outbox.py`, `reconfirm.py` | SQLAlchemy | Idempotent events, the queue, outbox rows, the 24 h re-confirm sweep |

**`safety/`: guardrails**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `approval_tokens.py` | cryptography + stdlib `hmac`, `hashlib`, `base64` | Mint and verify short-lived send tokens |
| `totp.py` | stdlib `hmac`, `hashlib`, `base64` | 6-digit dashboard login codes (no TOTP library) |
| `idempotency.py` | stdlib `hashlib` | Idempotency keys |
| `caps.py`, `killswitch.py` | SQLAlchemy | Daily caps, card reservations, the kill switch |
| `sanitize.py`, `claims.py`, `citations.py`, `email_checks.py`, `approval_window.py`, `mcp_keys.py` | stdlib (`re`) | Text cleaning, claim/number checks, email checks, MCP keys |

**`llm/`: the only AI gateway**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `client.py` | **OpenAI SDK**, Pydantic, OpenTelemetry | Every structured AI call |
| `agent_runtime.py` | Pydantic, OpenTelemetry | Capped agent loop (Job Finder, Prep Research) |
| `prompts.py` | **Jinja2** | Render `prompts/*.md` |
| `schema.py` | Pydantic | Convert models to OpenAI strict JSON schemas |
| `embeddings.py` | **fastembed** | Local text embeddings |
| `pricing.py` | stdlib | Token cost from `config` prices |

**`integrations/`: outside services**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `jobs_api.py` | httpx | JSearch API calls |
| `storage.py` | httpx | Supabase Storage REST API (or a local folder) |
| `vectors.py` | **qdrant-client** | Qdrant Cloud or local embedded Qdrant |
| `mcp_client.py` | **MCP SDK**, OpenTelemetry | Connect to an MCP server and call its tools |
| `mcp_process.py` | anyio | Start MCP servers as subprocesses |
| `mcp_scopes.py`, `telegram_sender.py`, `gmail_sender.py` | stdlib | Scope checks; the dispatcher's senders (they call the MCP client) |

**`monitoring/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `tracing.py` | **OpenTelemetry** (OTLP exporter → Langfuse) | Set up tracing |
| `redaction.py` | OpenTelemetry | Remove personal details from spans |
| `alerts.py`, `health.py`, `metrics.py` | SQLAlchemy | Alerts, health checks, dashboard metrics |

**`reference/` and `knowledge/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `reference/catalog.py` | stdlib (`json`) | Load the skills taxonomy |
| `reference/normalize.py` | stdlib `hashlib`, `urllib` | Normalise company / title / city names |
| `knowledge/learning.py` | stdlib + (via `integrations/vectors.py`) qdrant-client + fastembed | Load learning resources into Qdrant |
| `knowledge/lookup.py` | stdlib `asyncio` + qdrant-client (via `vectors.py`) | Skill lookup with payload filters, no AI |

**`profile/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `parser.py` | stdlib (AI through `llm/client.py`) | Resume → draft facts |
| `items.py`, `confirm.py`, `preferences.py`, `structure.py` | SQLAlchemy | Profile items, confirmation, preferences |

**`sources/`: finding jobs**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `finder_agent.py` | Pydantic (agent decision schema) | Job Finder agent |
| `role_expander.py` | Pydantic | Related-titles AI call |
| `enrich_lookup.py` | httpx, stdlib `html` | Free full-JD lookup via Greenhouse / Lever / Ashby APIs |
| `cursors.py`, `dedup.py`, `queue_rank.py` | SQLAlchemy | Cursors, deduplication, queue ranking |
| `base.py`, `jobs_api.py`, `job_finder.py`, `jd_enrich.py`, `shared_links.py` | stdlib | Source interface and the three sources |

**`matching/`: scoring**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `extract.py` | SQLAlchemy (cache) | Requirement extraction with its cache |
| `profile_view.py` | SQLAlchemy | Read the confirmed profile for scoring |
| `compute.py`, `embed_score.py`, `gap.py` | stdlib (embeddings via `llm/embeddings.py`, vectors via `integrations/vectors.py`) | Score, stage A, skills to learn |
| `hard_filters.py`, `gap_resources.py` | stdlib | Filters, learning links |

**`resume/`: tailoring and PDFs**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `render.py` | **Jinja2** + **Tectonic** (in `sandbox-exec` / Docker) | Build the PDF |
| `parse_check.py` | **pypdf** | Read the PDF text back |
| `preview.py` | **pypdfium2** (+ Pillow) | Page-1 PNG preview |
| `doc.py` | SQLAlchemy | Load/save the structured resume |
| `tailor.py`, `tex_editor.py`, `review.py`, `verifier.py`, `tex_versions.py`, `tex_import.py`, `skill_additions.py`, `ops.py`, `coverage.py`, `diff.py`, `comparison.py`, `profile_drafts.py` | stdlib (`re`, `difflib`); AI through `llm/client.py` | The edit → verify → review loop and helpers |

**`cards/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `transitions.py`, `messages.py`, `reminders.py` | SQLAlchemy | State changes + outbox rows, card messages, reminders |
| `render.py` | stdlib `html` | Telegram HTML card text and buttons |
| `build.py` | stdlib `asyncio` | Build a card |
| `states.py`, `approvals.py`, `rescore.py` | stdlib | State machine, version checks, re-scoring |

**`hr_email/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `service.py`, `address_finder.py`, `email_writer.py`, `drafts.py`, `send.py` | stdlib (AI through `llm/client.py`, DB through `db/queries/`) | Address, writing, drafts, send approval |

**`prep/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `ingest.py` | **pypdf**, stdlib `subprocess` | Split books at headings |
| `research.py` | Pydantic | Prep Research agent decisions |
| `service.py`, `qa_bank.py`, `skill_notes.py`, `retrieve.py` | stdlib (Qdrant + fastembed through shared modules) | The pack, Q&A bank, notes, retrieval |
| `qa_generator.py`, `dsa_picker.py`, `study_plan.py`, `marks.py` | stdlib | Q&A AI call, DSA patterns, study plan, known/weak marks |

**`chat/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `job_resolver.py`, `memory.py` | SQLAlchemy | Which job you mean; chat history |
| `service.py`, `context.py`, `policy.py`, `proposal_service.py`, `resume_edit.py` | stdlib (AI through `llm/client.py`) | Job Chat |

**`bot/`**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `callbacks.py` | OpenTelemetry | Button taps (traced) |
| `router.py`, `commands.py`, `messages.py`, `auth.py` | stdlib | Routing, commands, texts, chat-id check |

**`run/`: the batch run**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `graph.py` | **LangGraph**, OpenTelemetry | The run graph and checkpoints |
| `lease.py`, `stop_rules.py`, `steps/serve_queue.py`, `steps/summary.py` | SQLAlchemy | Run lease, stop rules, queue, summary |
| `pipeline.py`, `steps/fetch.py`, `steps/common.py` | OpenTelemetry | One job through the pipeline; fetching (traced) |
| `context.py`, `deps.py`, `state.py`, `share.py`, `steps/preflight.py`, `steps/screen.py`, `steps/process_job.py`, `steps/drain.py`, `steps/checks.py` | stdlib | Run state and the other steps |

**`servers/`: MCP servers (separate processes)**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `telegram/server.py` | **MCP SDK**, Pydantic | Telegram tool server |
| `telegram/tools.py` | httpx | Telegram Bot API calls |
| `gmail/server.py` | **MCP SDK**, Pydantic, httpx | Gmail tool server |
| `gmail/tools.py` | httpx, stdlib `email`, `base64` | Gmail API: drafts, Sent search, send |
| `gmail/auth.py` | cryptography | Encrypted refresh token |
| `gmail/oauth_setup.py` | httpx | One-time Google sign-in (`make gmail-auth`) |
| `gmail/token_verify.py` | stdlib | Check the send token |
| `web/server.py` | **MCP SDK**, Pydantic | Web tool server |
| `web/search.py` | httpx | OpenAI `web_search` tool over HTTP |
| `web/fetch.py` | httpx, stdlib `html.parser`, `urllib.robotparser` | Safe page fetch |
| `web/ssrf_guard.py` | stdlib `ipaddress`, `socket` | Block private / internal addresses |
| `*/auth.py` (telegram, web) | stdlib `hmac` | Check the caller's MCP key |

**`web/`: dashboard**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `main.py` | **FastAPI**, Starlette (sessions, middleware), Uvicorn | The app |
| `auth.py` | FastAPI | Login and session checks |
| `deps.py` | FastAPI, SQLAlchemy | Shared request dependencies |
| `login_guard.py` | SQLAlchemy | Lockout after failed codes |
| `routes/*.py` (`jobs`, `profile`, `preferences`, `prepare`, `resume`, `runs`, `settings`, `health`, `login`) | FastAPI, SQLAlchemy (`preferences` also Pydantic) | Pages and actions |
| `templates/ui/*.html` (outside `src/`) | **Jinja2** + **HTMX** | The HTML pages |

**`worker/` and `dispatcher/`: the processes**

| File | Frameworks / libraries | Used for |
|---|---|---|
| `worker/main.py` | **LangGraph** (Postgres checkpointer), psycopg, psycopg_pool | Start the worker |
| `worker/telegram_poller.py` | httpx | Telegram long polling |
| `worker/queue_loop.py` | SQLAlchemy, psycopg | Claim and run work items |
| `worker/scheduler.py` | stdlib `asyncio` | Timers |
| `dispatcher/main.py` | psycopg (`LISTEN/NOTIFY`) | Start the dispatcher, wake on new rows |
| `dispatcher/delivery.py` | SQLAlchemy, OpenTelemetry | Deliver outbox rows in order |

**Outside `src/`**

| Path | Frameworks / tools |
|---|---|
| `app.py` | Vercel's Python runtime imports the FastAPI `app` |
| `scripts/*.py` | The same libraries as the code they call (SQLAlchemy, qdrant-client, pypdf…) |
| `tests/` | pytest, pytest-asyncio |
| `evals/` | pytest-style runners + thresholds in YAML |
| `deploy/` | supervisor, launchd, macOS `sandbox-exec` profile for Tectonic |
| `templates/resume/resume.tex.j2` | Jinja2 → LaTeX (built by Tectonic) |
| `Makefile` | GNU make + uv |

---

# Part 2: Build it yourself (a tutorial)

This is the order to rebuild JobPilot from an empty folder. It follows the project's phases (P0-P8). **Every step ends
with a check you can run.** Don't move on until it passes; each step leaves something that works.

The snippets are simplified versions of the real files. Open the real file next to each step to see the full version.

## 2.0 What to learn first

| Topic | Why | Enough to know |
|---|---|---|
| Python 3.12 `async`/`await` | Everything is async (DB, HTTP, AI) | `asyncio.gather`, async context managers |
| Postgres + SQLAlchemy Core (not the ORM) | All state lives in Postgres | `Table`, `select`, `update().where().returning()`, transactions |
| Alembic | Database migrations | `revision --autogenerate`, reading the generated file |
| Pydantic v2 | Every AI output and data type | `BaseModel`, `Field`, validators |
| OpenAI Responses API | All AI calls | `responses.create`, `json_schema` strict output, `web_search` tool |
| LangGraph basics | The run graph + checkpoints | `StateGraph`, nodes, conditional edges, Postgres checkpointer |
| MCP Python SDK | Telegram/Gmail/Web servers | a server with tools over stdio, a client session |
| FastAPI + Jinja + HTMX | Dashboard | routes, templates, `hx-get`/`hx-post` |
| Telegram Bot API | Messages and buttons | `getUpdates`, `sendMessage`, inline keyboards, `callback_query` |
| Embeddings + Qdrant | Similarity, knowledge base | cosine similarity, collections, payload filters |
| pytest + pytest-asyncio | Tests for every step | fixtures, async tests |

## Step 1: The skeleton (P0)

**Goal:** a project that installs, lints, type-checks and runs an empty test.

```bash
mkdir JobPilot && cd JobPilot
uv init --package --name jobpilot --python 3.12
uv add fastapi uvicorn sqlalchemy[asyncio] "psycopg[binary,pool]" pydantic pydantic-settings pyyaml httpx jinja2
uv add --group backend alembic openai langgraph langgraph-checkpoint-postgres mcp fastembed qdrant-client supervisor
uv add --group dev pytest pytest-asyncio ruff mypy
mkdir -p src/jobpilot/{core,models,db,queue,safety,llm,integrations,monitoring} config prompts templates tests/unit
```

Create:

- `config/defaults.yaml`: models, caps, `shadow_mode: true`, timezone.
- `.env.example`: secret **names** only (`OPENAI_API_KEY=`, `TELEGRAM_BOT_TOKEN=`, `DATABASE_URL=`…).
- `src/jobpilot/core/config.py`: one place that reads YAML and `.env`.

```python
# core/config.py (simplified)
from functools import lru_cache
from pathlib import Path
import yaml
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):               # secrets, from .env
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/jobpilot"
    openai_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None

class Config(BaseModel):                     # tunables, from config/defaults.yaml
    shadow_mode: bool
    match_threshold: int
    models: dict[str, str]
    prompt_versions: dict[str, int]
    def model_for(self, task: str) -> str:
        return self.models.get(task, self.models["default"])

@lru_cache
def get_settings() -> Settings: return Settings()

@lru_cache
def get_config() -> Config:
    return Config.model_validate(yaml.safe_load((REPO_ROOT / "config/defaults.yaml").read_text()))
```

- `core/clock.py`: `now()` wrapper so tests can fake time. **Never call `datetime.now()` elsewhere.**
- `Makefile` with `check: lint typecheck test`.

**Check:** `make check` passes.

## Step 2: Database, migrations, version-checked updates (P0)

**Goal:** tables for `events`, `work_items`, `outbox`, `runs`, `approvals`, `source_cursors`, and one helper for safe
updates.

```python
# db/tables.py (a small part)
from sqlalchemy import MetaData, Table, Column, BigInteger, Text, Integer, DateTime, JSON, Index, text
metadata = MetaData()

outbox = Table("outbox", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("partition_key", Text, nullable=False),   # e.g. "card:42"
    Column("seq", Integer, nullable=False),          # order inside the partition
    Column("kind", Text, nullable=False),            # telegram | gmail
    Column("payload", JSON, nullable=False),         # never holds tokens
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("external_id", Text),                     # Telegram message id / Gmail id
    Column("next_attempt_at", DateTime(timezone=True)),
)

runs = Table("runs", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("status", Text, nullable=False),          # running | draining | paused | finished
    Column("lease_holder", Text), Column("heartbeat_at", DateTime(timezone=True)),
    # only ONE active run can exist:
    Index("one_active_run", "user_id", unique=True,
          postgresql_where=text("status in ('running','draining')")),
)
```

The version check every update uses (this is the real `db/queries/base.py`):

```python
async def versioned_update(conn, table, row_id, expected_version, **values) -> int:
    pk = next(iter(table.primary_key.columns))
    result = await conn.execute(
        update(table)
        .where(pk == row_id, table.c.version == expected_version)
        .values(version=table.c.version + 1, **values)
        .returning(table.c.version))
    new_version = result.scalar_one_or_none()
    if new_version is None:
        raise VersionConflict(f"{table.name} {row_id}: expected version {expected_version}")
    return new_version
```

Run `alembic revision --autogenerate -m core_tables` and **read the generated file** before applying it (in this
project an unfiltered autogenerate once dropped LangGraph's checkpoint tables; `db/migrations/env.py` now filters
them out).

**Check:** a test that updates a row with the wrong version gets `VersionConflict`.

## Step 3: Events, the work queue and the outbox (P0)

**Goal:** the three tables that make everything reliable.

1. **Events** (`queue/events.py`): every input (Telegram update, dashboard click) is inserted with an
   `idempotency_key`. Insert with `ON CONFLICT DO NOTHING`; if nothing was inserted, it's a repeat, so stop.
2. **Work queue** (`db/queries/work_items.py`): claim work like this:

```sql
UPDATE work_items SET status='running', locked_until = now() + interval '2 minutes', attempts = attempts + 1
WHERE id = (
  SELECT id FROM work_items
  WHERE status = 'pending' AND run_after <= now()
  ORDER BY id
  FOR UPDATE SKIP LOCKED      -- two workers never grab the same row
  LIMIT 1)
RETURNING *;
```

3. **Outbox** (`queue/outbox.py`): features never call Telegram. They write a row **in the same transaction** as their
   state change:

```python
async with transaction() as conn:
    new_version = await cards_repo.set_status(conn, card_id, "resume_approved", expected_version=v)
    await outbox.telegram_message(conn, "Resume v3 approved ✅",
        partition_key=outbox.card_partition(card_id),
        idempotency_key=f"approved:{card_id}:{new_version}")
# commit → both saved, or neither. NOTIFY 'outbox' wakes the dispatcher.
```

**Check:** a test writes a state change + outbox row, rolls back, and finds neither.

## Step 4: The dispatcher (P0)

**Goal:** the only process that sends things.

```python
# dispatcher/delivery.py (the idea)
async def deliver_ready(self):
    heads = await outbox_repo.partition_heads(conn)    # the lowest pending seq of each partition
    for row in heads:                                  # partitions are independent
        try:
            external_id = await self.senders[row["kind"]].deliver(row["payload"])
            await outbox_repo.mark_delivered(conn, row["id"], external_id)
        except Exception as exc:
            await outbox_repo.mark_failed(conn, row["id"], backoff(row["attempts"]))  # partition pauses
```

`dispatcher/main.py` runs `LISTEN outbox` on a session connection and calls `deliver_ready()` on each notification and
every few seconds. Edits resolve their target message id **at delivery time** from the earlier row's `external_id`.

**Check:** two rows in one partition, the first failing → the second waits; after the first succeeds, both are sent
in order.

## Step 5: The Telegram MCP server and the poller (P0)

**Goal:** messages in and out.

1. Create a bot: Telegram → search **@BotFather** → `/newbot` → pick a name → copy the token into `.env` as
   `TELEGRAM_BOT_TOKEN`. Send your bot a message, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, and copy
   `message.chat.id` into `TELEGRAM_CHAT_ID`.
2. **MCP server** (`servers/telegram/server.py`) with the MCP Python SDK. Each tool checks the chat id:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("telegram")

@mcp.tool()
async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    if chat_id != ALLOWED_CHAT_ID:
        return {"ok": False, "error": "chat not allowed"}
    r = await http.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
    return {"ok": True, "message_id": r.json()["result"]["message_id"]}

if __name__ == "__main__":
    mcp.run()          # stdio; the dispatcher starts it as a subprocess
```

3. **Poller** (`worker/telegram_poller.py`): loop on `getUpdates?offset=<last+1>&timeout=50`. For each update: check the
   chat id, store the event (key = `update_id`), queue a work item, then advance the offset.

**Check:** send "hi" twice with the same update id (`scripts/fake_update.py`) → one event row.

## Step 6: The run lease and a stub run graph (P0)

**Goal:** `/start` → exactly one run → one summary message.

```python
# run/graph.py (the shape; the real file adds pause/resume)
g = StateGraph(RunState)
for name, fn in [("preflight", preflight), ("serve_queue", serve_queue), ("fetch", fetch),
                 ("screen", screen), ("process_job", process_job), ("drain", drain), ("summary", summary)]:
    g.add_node(name, fn)
g.add_edge(START, "preflight")
g.add_conditional_edges("preflight", lambda s: "drain" if s.get("stop_reason") else "serve_queue")
g.add_edge("serve_queue", "fetch"); g.add_edge("fetch", "screen"); g.add_edge("screen", "process_job")
g.add_conditional_edges("process_job", lambda s: "drain" if s.get("stop_reason") else "process_job")
g.add_edge("drain", "summary"); g.add_edge("summary", END)
graph = g.compile(checkpointer=AsyncPostgresSaver(pool))   # resumes after a crash
```

`run/lease.py`: insert a `runs` row with status `running` (the unique partial index refuses a second one), heartbeat
every 15 s, and take over a lease whose heartbeat is older than 120 s. Start with every step a stub; `summary` writes one
outbox row.

**Check (P0 done):** `/start` gives one run, one summary from the outbox and one trace; a second `/start` while running
does nothing extra.

## Step 7: The LLM client and prompts (P0)

**Goal:** one function for all AI calls.

Prompt file format (`prompts/system/ping_v1.md`):

```markdown
## system
You are a health check. Reply with ok = true.
## user
{{ message }}
```

`llm/prompts.py` renders the pinned version with Jinja (`StrictUndefined`). `llm/client.py`:

```python
async def structured(self, task, prompt, output: type[T], *, run_id=None, max_tokens=None) -> LLMResult[T]:
    model = get_config().model_for(task)
    for attempt in (1, 2):                                       # one retry
        resp = await self.api.responses.create(
            model=model, store=False, max_output_tokens=max_tokens or 16000,
            input=[{"role": "system", "content": prompt.system},
                   {"role": "user", "content": prompt.user}],
            text={"format": {"type": "json_schema", "name": output.__name__,
                             "strict": True, "schema": strict_schema(output)}})
        cost = cost_usd(model, resp.usage)                       # from config pricing
        await record_usage(task, model, prompt.id, resp.usage, cost, run_id)   # llm_usage table
        try:
            return LLMResult(output.model_validate_json(resp.output_text), ...)
        except ValidationError as exc:
            last = exc
    raise LLMInvalidOutput(f"{task}: {last}")
```

Add tracing (`monitoring/tracing.py`, OpenTelemetry → Langfuse Cloud): one span per call with model, prompt id, tokens,
cost and "valid".

**Check:** `make llm-ping` prints a valid object and a row appears in `llm_usage`.

## Step 8: Profile and structured resume (P1)

**Goal:** your facts, confirmed, as the single source of truth.

1. `profile/parser.py` (🤖 `profile_extract`): resume text → `ExtractedProfile` (skills, projects, roles, metrics),
   each with evidence text.
2. `profile_items` table: `kind`, `text`, `evidence`, `confirmed_at`. **Only confirmed items are used anywhere.**
3. `resume/tex_import.py` + `scripts/import_resume.py`: read your own `.tex` into `ResumeDoc` (sections → entries →
   bullets, each with the profile item ids it rests on).
4. `resume/render.py`: compile with **Tectonic** in a sandbox that has no network.
5. Dashboard page to confirm items (`web/routes/profile.py`) and set preferences (`web/routes/preferences.py`).

**Check:** the PDF rendered from data looks like your original, and every bullet points at confirmed items.

## Step 9: Job sources, cursors and dedup (P2)

**Goal:** jobs in, each exactly once.

```python
# sources/base.py
class Source(Protocol):
    name: str
    async def fetch(self, cursor: dict) -> tuple[list[JobPosting], dict]: ...
```

- `sources/jobs_api.py`: JSearch (sign up at RapidAPI → subscribe to JSearch → copy the key into `JSEARCH_API_KEY`). The
  cursor holds the last posting time + seen ids. Count every call in `api_usage` (6/day on the free plan).
- `sources/shared_links.py`: a link you paste becomes a posting.
- `sources/cursors.py`: save a source's new cursor **in the same transaction** as its jobs. A failure leaves the cursor
  where it was, so nothing is skipped.
- `reference/normalize.py`: company/title/city normalisation ("Swiggy Pvt. Ltd." → "swiggy").
- `sources/dedup.py`: board id → `company_norm + title_norm + city` → near-duplicate JD text. A match inserts a
  `job_sources` sighting, never a second job.

**Check:** replay the same job from 3 sources → one job, three sightings.

## Step 10: Filters, embeddings, extraction, score, gap (P2)

**Goal:** a deterministic score.

1. `matching/hard_filters.py`: blacklist and titles-to-avoid, each with a logged reason.
2. `llm/embeddings.py`: fastembed `BAAI/bge-small-en-v1.5` (local, free, 384 dims). `matching/embed_score.py`: cosine
   similarity profile ↔ job, used to **order** jobs.
3. `matching/extract.py` (🤖 `extract_requirements`): JD → `Requirements`. **Cache** by
   `(sha256(JD), prompt version, model id)`: the same JD is never paid for twice.
4. `matching/compute.py` (⚙️): for each dimension, the share of required items you have, with partial credit by meaning
   (embedding similarity scaled between `phrase_low` and `phrase_high` in `config/scoring.yaml`).

```python
# the heart of compute(), simplified (the real code uses SkillMatcher.match() and per-dimension helpers)
score = 0.0
for dim, weight in effective_weights(lists, weights()).items():
    asked = lists[dim]                                   # e.g. ["python", "pytorch", "kafka"]
    if not asked:
        continue
    credit = sum(await matcher.credit(skill, dim) for skill in asked)   # 1.0 found, 0..1 partial, 0 missing
    score += weight * credit / len(asked)
```

5. `matching/gap.py`: each missing item is worth `weight / len(asked)` minus partial credit. Rank by
   points × learnability. `matching/gap_resources.py`: links **only** from `Data/learning/`.

**Check:** the same JD + profile gives the same score twice; the gap never lists a skill you have; run the extraction
eval (`evals/`) on hand-labelled JDs **before** tuning the score.

## Step 11: The web MCP server and the Job Finder agent (P3)

**Goal:** leads from web search, safely.

1. `servers/web/search.py`: one Responses API call with `tools=[{"type": "web_search"}]` and
   `tool_choice="required"`. Keep only URLs the tool returned (citations), never URLs the model typed.
2. `servers/web/ssrf_guard.py` + `fetch.py`: resolve DNS, refuse private/loopback/link-local ranges, re-check every
   redirect, connect to the checked IP, cap size and time, obey `config/fetch_rules.yaml` (deny LinkedIn/Naukri/Indeed).
3. `llm/agent_runtime.py`: caps before every step (see 1.5).
4. `sources/finder_agent.py`: the decide-loop:

```python
class Decision(BaseModel):
    action: Literal["search", "fetch", "finish"]
    query: str | None = None
    url: str | None = None
    leads: list[Lead] = []

observations = [await runtime.search(q) for q in code_chosen_queries]   # code searches first
while True:
    d = await runtime.decide(prompt(task, context, observations), Decision, run_id=run_id, purpose=task)
    if d.action == "finish":
        break
    if d.action == "search":
        observations.append(await runtime.search(d.query))
    elif d.url in urls_from_search:                          # only fetch what search returned
        observations.append(await runtime.fetch(d.url))
# then CODE validates every lead: real URL from search, new, not a listing page, company+title match
```

**Check:** labelled leads eval; every SSRF URL and deny-listed domain test is refused; the agent never filters or scores.

## Step 12: Resume tailoring (P4, resume v2)

**Goal:** a per-job resume that never invents.

1. `resume/tex_versions.py`: copy the base `.tex` for each job (`resume_versions.tex`).
2. `resume/tex_editor.py`: 🤖 `propose()` returns `TexEditPlan(edits=[{find, replace, section, reason, cites}])`.
   ⚙️ `apply()` accepts an edit only if:
   - `find` appears exactly once and is not in the header, Education or preamble;
   - `replace` uses only LaTeX commands the base already uses, no `\input`, `\write`, `\def`…, balanced braces;
   - every number, skill and title word in `replace` appears in a cited confirmed fact (`safety/claims.py`).
3. `resume/verifier.py`: build the PDF, read the text back: 1 page, name + every section in order, no broken
   characters, every accepted edit visible.
4. `resume/review.py`: 🤖 score 0-100 + fixes.
5. `resume/tailor.py`: the loop in section 1.5 (max rounds, stop at 95, stop after 2 rounds without improvement, keep the
   best).

**Check:** tailoring eval with zero uncited claims; LaTeX payload tests (`\input{/etc/passwd}`) all refused;
`make resume-trial` tests the loop without cards.

## Step 13: Cards and buttons (P4)

1. `cards/states.py`: allowed transitions as a table; anything else raises.
2. `cards/transitions.py`: each transition = version-checked update + outbox rows, in one transaction.
3. `cards/render.py`: Telegram HTML in sections; buttons carry short opaque ids (`core/ids.py`) because
   `callback_data` is at most **64 bytes**. Approve names the exact version.
4. `cards/build.py`: score → tailor → store PDF → card + outbox, plus a daily-capacity reservation
   (`card_reservations`) so two builders can't exceed the cap.
5. `bot/callbacks.py`: tap → event (idempotent) → transition. Stale version → refused, newest shown.

**Check:** stale Approve is refused and shows the new draft; a double tap is a no-op; a card can be acted on in under a
minute.

## Step 14: HR email, exactly once (P5)

1. Gmail setup: Google Cloud Console → new project → **APIs & Services → Library → Gmail API → Enable** → **OAuth
   consent screen** (External, add your address as a test user, then **Publish app → In production** so refresh
   tokens don't expire after 7 days) → **Credentials → Create credentials → OAuth client ID → Desktop app** → copy the
   **Client ID** and **Client secret** into `.env` as `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET`. Run
   `make gmail-token-key` (put its output in `.env` as `GMAIL_TOKEN_KEY`), then `make gmail-auth`.
2. `hr_email/address_finder.py`, `hr_email/service.py`: address order and domain checks (section 1.6 F).
3. `hr_email/email_writer.py` (🤖) + `safety/email_checks.py` (⚙️), one retry with the exact problems.
4. `hr_email/send.py`: approvals row + card → `sending` + outbox row, one transaction.
5. `safety/approval_tokens.py`: HMAC over (job id, draft id, version, expiry), minted by the dispatcher at delivery,
   never stored. `servers/gmail/token_verify.py` checks it before `send_draft`.
6. Crash safety: before retrying, `find_sent` searches Sent by the `X-JobPilot-Job` header; `sent_emails.job_id` is
   UNIQUE.

**Check:** kill the dispatcher after Gmail accepts and before the DB write → exactly one email; a send without a token is
refused by the server; shadow mode logs instead of sending.

## Step 15: Job Chat (P6)

1. `chat/job_resolver.py`: replied-to card → `/chat Name` → a name in the text → else offer 2-3 recent cards.
2. `chat/context.py`: profile and drafts as instructions; the JD in a fenced data block.
3. `chat/service.py`: one `JobChatDecision`, then code executes the tool.
4. `chat/proposal_service.py`: tier-2 changes become proposals with Confirm/Cancel.

**Check:** 50 scripted requests including injected JDs; no injected JD changes the profile or preferences without
Confirm.

## Step 16: Knowledge base and Prepare (P7)

1. `prep/ingest.py` + `make ingest-corpus`: split books **at headings** (PDF bookmarks, or a heading heuristic for books
   without them), keep page numbers, tag `skill_ids`, embed into Qdrant `prep_chunks`. Never fixed-size chunks.
2. `knowledge/learning.py` + `make ingest-learning`: the 755 resources into `learning_resources`.
3. `prep/skill_notes.py` + `make skill-notes`: one note per skill, once.
4. `prep/service.py`: storage first, then one 🤖 call for missing Q&A groups, then save to the bank.
5. `prep/research.py`: the Prep Research agent on the shared runtime.

**Check:** ≥ 90% of answers grounded with a valid citation (`make eval-prep-live`).

## Step 17: The dashboard (P1-P7, deployed in P8)

- FastAPI app (`web/main.py`) with Jinja templates in `templates/ui/` and HTMX for partial updates.
- TOTP login (`web/auth.py`): `make totp-secret` → scan into Google Authenticator. Without `DASHBOARD_TOTP_SECRET`
  the app answers 503.
- On Vercel it connects through Supabase's **transaction pooler** (`DATABASE_POOLER_URL`, NullPool, no prepared
  statements). Dashboard actions are written as events, exactly like taps.

**Check:** login works, a wrong code 5 times locks you out for 15 minutes, every page renders.

## Step 18: Observability, tests and evals (all phases)

- One trace per run, one span per job, AI call, tool call and tap (`monitoring/tracing.py`).
- Alerts through the outbox (`monitoring/alerts.py`): budget 80%, quota 80%, 3 failures in a row, stuck run, outbox not
  draining, unsent approved email.
- Tests: `tests/unit` (logic), `tests/concurrency` (double `/start`, repeated update, crash mid-send, edit before send),
  `tests/integration` (recorded fixtures), `tests/contract` (nightly, real APIs), `tests/e2e`.
- Evals: `evals/` with thresholds in `evals/thresholds.yaml`. A changed prompt is a **new file** (`_v3.md`) and re-runs
  the evals.

## Step 19: Go live (P8)

1. Supabase: new project → **Project Settings → Database** → copy the session pooler URI into `DATABASE_URL` and the
   transaction pooler URI into `DATABASE_POOLER_URL`. Back up, then `make migrate CONFIRM=yes`. Then
   `make storage-setup`.
2. Qdrant Cloud: create a free cluster → copy the URL and API key into `QDRANT_URL`, `QDRANT_API_KEY`.
3. Vercel: import the repo, set the environment variables, region `bom1`.
4. Laptop: `make autostart-install` (launchd keeps worker + dispatcher running).
5. A shadow week (emails logged), then a canary (shadow off, 5 cards + 2 emails a day), then raise caps slowly.

---

# Part 3: What costs money and how to spend less

## 3.1 Every cost in the system

| Item | Paid? | How it's charged | Where it's counted |
|---|---|---|---|
| OpenAI `gpt-4o-mini` tokens | **Yes** | $0.15 per 1M input, $0.60 per 1M output, $0.075 per 1M cached input (`config/defaults.yaml` → `pricing`) | ✅ `llm_usage` table, run totals |
| OpenAI `web_search` tool | **Yes** | About $0.01 per search call, plus ~8.5k input tokens per search (measured 2026-10-06, `docs/decisions.md`) | ❌ **Not recorded anywhere** (see 3.4, fix 1) |
| JSearch jobs API | Free plan | 6 calls/day (~200/month); paid plans cost more | ✅ `api_usage` table |
| Embeddings (fastembed) | Free | Runs on your laptop CPU | — |
| Tectonic (PDF build) | Free | Laptop CPU | — |
| Supabase, Qdrant Cloud, Vercel, Langfuse | Free tiers today | Free until you outgrow a tier's limits | Each service's dashboard |
| Telegram, Gmail API | Free | — | — |
| Your laptop | Electricity | Runs worker + dispatcher | — |

So the **only real money** today is OpenAI: tokens **and web searches**.

## 3.2 Real numbers from your database

Read-only query of `llm_usage`, 2026-10-05 to 2026-10-07 (includes `make resume-trial` runs and rebuilds, not just
`/start`):

| Task | Calls | Input tokens | Output tokens | Cost (USD) | Share |
|---|---|---|---|---|---|
| `tex_editor` | 186 | 616,940 | 101,440 | 0.153 | 34% |
| `resume_review` | 173 | 467,167 | 75,044 | 0.115 | 26% |
| `salary_research` (feature since removed) | 174 | 331,740 | 13,121 | 0.058 | 13% |
| `job_finder` | 100 | 264,284 | 14,523 | 0.048 | 11% |
| `skill_note` | 38 | 117,804 | 44,306 | 0.044 | 10% |
| `extract_requirements` | 46 | 53,592 | 7,726 | 0.013 | 3% |
| everything else | 31 | ~71,000 | ~7,100 | 0.015 | 3% |
| **Total** | **748** | **~1.92M** | **~263k** | **$0.45** | |

The last 8 `/start` runs recorded between $0.0003 and $0.032 each in token cost.

**Two things these numbers show:**

1. **Cached input tokens are 0 in every one of the 748 calls.** OpenAI caches a prompt's opening part automatically
   when the prompt is long enough (1,024+ tokens) and its beginning is identical between calls. You're getting no
   discount at all (see fix 6).
2. **Web searches are missing from these numbers.** A full `/start` runs 48 lead searches plus about 2 per agent JD
   lookup plus 3+ per card for the HR address. At ~$0.01 each, **the searches likely cost 10-30× more than all the
   tokens of a run.** `docs/decisions.md` estimates about $1-2 per full `/start`, nearly all of it search.

## 3.3 The most expensive parts, ranked

Estimated for one full `/start` that makes 10 cards:

| Rank | Part | Why it costs | Rough cost per full run | Setting / code |
|---|---|---|---|---|
| 1 | 🔎 **Job Finder lead searches** | 48 web searches every run, whatever they find | ~$0.50-0.55 | `agents.job_finder.lead_searches_per_run: 48` |
| 2 | 🔎🕵️ **Agent JD lookups** | Up to 60 jobs × ~2 searches + up to 3 page reads + several AI steps | up to ~$1.20 at the cap | `max_jd_lookups_per_run: 60` |
| 3 | 🔎🕵️ **HR address search for every card** | The automatic draft runs 3+ searches + agent steps per card, even if you never email | ~$0.04 per card → ~$0.40 | `cards/build.py` (`email-auto` work item), `sources/finder_agent.py` → `CONTACT_SEARCHES` |
| 4 | 🤖 **Tailoring loop** | Up to 6 rounds × (editor + reviewer), each sending the whole `.tex` + facts + JD | ~$0.01-0.02 per card | `resume.review_max_rounds: 6`, `review_pass_score: 95` |
| 5 | 🤖 HR email writer for every card | 1-2 calls per card, even unused | small | `cards/build.py` |
| 6 | 🤖 Skill notes | Long outputs (~1,200 tokens each), but once per skill | one-off | `prep/skill_notes.py`, `MAX_NEW_NOTES = 4` |
| 7 | 🤖 Requirement extraction | One call per new JD, cached | ~$0.0003 per job | `matching/extract.py` |
| 8 | 🤖 Job Chat, Prep | On demand | small | `budgets.job_chat_*`, `agents.prep_research` |

Rule of thumb: **anything that searches the web is expensive; anything that only calls `gpt-4o-mini` is cheap.**
Tokens are cheap but add up when a loop resends the same long text.

## 3.4 How to cut costs (with the exact setting or file)

Ordered by saving per effort.

### Fix 1: Measure web search spend (do this first)

You can't reduce what you can't see. Record every search in `api_usage` (provider `openai_web_search`) and add
`searches × $0.01 + search tokens` to the run's cost, so `/status`, the run summary and the budget alert include it.

- Where: `llm/agent_runtime.py` → `AgentRuntime.search()` (it already counts `use.searches`; write it to the DB) or the
  web server's `search` tool.
- Then add `web_search_per_call_usd` to `config/defaults.yaml` → `pricing`.

### Fix 2: Fewer lead searches per run (one line, biggest saving)

```yaml
# config/defaults.yaml
agents:
  job_finder:
    lead_searches_per_run: 16   # was 48 → about a third of the search cost
```

The searches **rotate** through the pool across runs (`lead_queries(prefs, offset, budget)`), so over 3 days you still
cover every title × site × city. Check recall with the shared-links metric after a week.

### Fix 3: Swap web search for free public job-board APIs (the biggest structural saving)

Greenhouse (`boards-api.greenhouse.io/v1/boards/<company>/jobs`), Lever (`api.lever.co/v0/postings/<company>`) and
Ashby (`api.ashbyhq.com/posting-api/job-board/<company>`) publish **free JSON** for each company. JobPilot already reads
them for single jobs in `sources/enrich_lookup.py`. A **company watchlist source** (the open "20-30 jobs a day" plan)
would:

- list every open role at, say, 100-200 companies you care about, **for free**, with the **full JD included**;
- remove most agent JD lookups (fix 4) because the JD comes with the posting.

Build it as a new `sources/watchlist.py` implementing `Source.fetch(cursor)` with a list of companies in
`config/watchlist.yaml`. Keep web search for discovery of new companies at a low budget.

### Fix 4: Fewer agent JD lookups

```yaml
agents:
  job_finder:
    max_jd_lookups_per_run: 15   # was 60
```

And only look up the JD for jobs that can realistically make a card: in `run/pipeline.py`, call `find_full_jd()` only
for jobs whose stage-A similarity is in the top N for this run, or score on the snippet first and look up only if the
snippet score is within ~15 points of your bar.

### Fix 5: Draft the HR email only when you ask

Today every card triggers an HR address search + email draft in the background (`cards/build.py`, work item
`email-auto:<job>:<version>`). Most cards will never get an email. Make it on demand: start the draft when you tap
**I applied** or **Email HR**. You wait ~20 seconds once, and you save 3+ web searches and 1-2 AI calls on every other
card. Also cache found addresses per company (`hr_email/service.py` already reuses an address used before; extend it to
reuse *searched* addresses for 30 days).

### Fix 6: Get prompt caching to work (cached input is half price)

All 748 calls show `cache_read_tokens = 0`. OpenAI caches automatically when:

- the prompt is at least 1,024 tokens, and
- the **beginning** of the prompt is identical to a recent call.

So put the long, unchanging parts **first** and the job-specific parts **last**:

```
system:  instructions (fixed)  +  your confirmed profile facts (same for every job)   ← cached
user:    the job (title, JD)   +  this round's .tex  +  feedback                      ← changes
```

For `tex_editor` and `resume_review`, the 6 rounds of one card share the instructions, facts and JD, so a stable
prefix could be cached from round 2. Change this in the prompt files (a **new version**, e.g.
`prompts/resume/tex_edit_v2.md`, `review_v3.md`), pin it in `config/defaults.yaml`, and re-run the evals. Then watch
`llm_usage.cache_read_tokens` climb.

### Fix 7: Shorter tailoring loops

```yaml
resume:
  review_pass_score: 90   # was 95; 90+ is already "good with small fixes" in the reviewer's own scale
  review_max_rounds: 3    # was 6
```

The loop already stops after 2 rounds without improvement. Also send the editor **only the editable sections** of the
`.tex` (not the preamble, header and Education it may not touch), which cuts the largest input in `tex_editor`.

### Fix 8: Tailor on demand instead of for every card

The biggest token user is tailoring every card up front. Alternative (also in the open plan): send the card with the
**base resume** and a **Tailor** button; run `resume/tailor.py` only for jobs you're interested in. If you act on 3 of
10 cards, tailoring tokens drop by ~70%.

### Fix 9: Use the Batch API for work that can wait

OpenAI's Batch API costs about half and returns within 24 hours. Good fits: `skill_note` (`make skill-notes`),
`qa_bank` filling (`make qa-bank`), and requirement extraction for queued jobs that won't be carded today. Add a
`batch=True` path in `llm/client.py` used only by those scripts.

### Fix 10: Trim inputs

- Cap the JD sent to `extract_requirements` (strip boilerplate like "About us", "Equal opportunity", benefits).
- Send the agent only the last few observations with short snippets (already done for leads; check the JD lookup).
- Remove the leftover `salary_research` usage: no code calls it any more (the 174 calls are history), so nothing to do
  except not to bring it back.

### Fix 11: Set a budget so nothing surprises you

```yaml
budgets:
  tokens_per_day: 1500000      # was null (no limit). Runs stop gracefully when reached
```

And, after fix 1, a daily **USD** cap that includes searches, checked in `run/stop_rules.py` (`budget` stop reason
already exists) and alerted at 80% by `monitoring/alerts.py`.

### Things that are already cheap (leave them)

- Embeddings are local (no API).
- Requirement extraction is cached by JD hash.
- The score, gap, learning links, dedup, filters and the HR address choice are plain code.
- LinkedIn leads are read from search titles by a regex, with no AI step.
- Prepare reuses stored notes and Q&A; one AI call writes only missing groups.

## 3.5 A cheaper configuration you can copy

Changes to `config/defaults.yaml` that need **no code changes** (restart the backend afterwards with
`launchctl kickstart -k gui/$(id -u)/com.jobpilot.backend`):

```yaml
budgets:
  tokens_per_day: 1500000

agents:
  job_finder:
    lead_searches_per_run: 16      # 48 → 16
    max_jd_lookups_per_run: 15     # 60 → 15
    max_searches_per_run: 60       # 200 → 60 (hard ceiling for leads + lookups)
    max_tokens_per_run: 600000     # 2M → 600k

resume:
  review_pass_score: 90            # 95 → 90
  review_max_rounds: 3             # 6 → 3
```

Rough effect on a full 10-card `/start`: web searches from ~130-170 down to ~45-60, and tailoring tokens roughly
halved, so the run's cost drops from about **$1-2 to about $0.40-0.70**. With fixes 3, 5 and 8 (code changes), a
typical day can run **under $0.20**.

Then watch, for a week:

- the run summary's cost (after fix 1, it includes searches);
- **recall** on the dashboard (jobs you shared that JobPilot already had): if it drops below 70%, raise
  `lead_searches_per_run` a little or add companies to the watchlist (fix 3);
- the resume review scores on cards: if many land below 90, raise `review_max_rounds` to 4.
