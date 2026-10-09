# How JobPilot Works

This guide explains, in plain English, what happens from the moment you send `/start` on Telegram to the
moment you apply for a job. Each step names the file that does the work, so you can open it and read along.
All code paths are inside `src/jobpilot/`.

---

## The big picture

JobPilot is your personal job-hunting assistant. It finds new jobs, checks how well each one fits you, writes a
version of your resume tailored to the job, and sends you a short "job card" on Telegram. **You** decide what
to do with each card: approve the resume, apply, and (if you want) send an email to HR.

It never applies for you and never sends anything in your name without your tap.

It has three parts that run all the time:

| Part | Where it runs | What it does |
| --- | --- | --- |
| **Worker** | your laptop | Reads your Telegram messages, runs the job search, scores jobs, writes resumes |
| **Dispatcher** | your laptop | Sends everything out: Telegram messages, PDFs, HR emails |
| **Dashboard** | Vercel (a website) | Lets you see your jobs, profile, preferences and resumes in a browser |

They share one database (Supabase). They never talk to each other directly: one writes a row in the
database, another reads it.

A small helper called **launchd** keeps the worker and dispatcher running on your laptop. When the laptop is
asleep, nothing runs; Telegram keeps your messages for 24 hours, so nothing is lost.

---

## Part 1: You send `/start`

### Step 1. The worker reads your message

- **File:** `worker/telegram_poller.py`
- The worker keeps asking Telegram "any new messages?" (this is called *long polling*). When your `/start`
  arrives, it saves it in the `events` table.
- Every Telegram message has a unique number. If the same message arrives twice, the second copy is ignored.
- The message becomes a small **work item** in a queue. The worker picks up work items one by one
  (`worker/queue_loop.py`).

### Step 2. The command is understood

- **Files:** `bot/router.py`, `bot/commands.py`
- The router looks at the message: is it a command (`/start`, `/stop`, `/status`, `/prefs`), a button tap, a
  job link you pasted, or a chat message about a job? It sends it to the right place.
- `/start` creates a new **run**. Only one run can happen at a time (`run/lease.py`); a second `/start`
  while one is running does nothing extra.
- A plain `/start` uses your "cards per day" preference as the limit. `/start 3` limits this run to 3 cards.
- You immediately get "Run #N started (up to N cards)".

---

## Part 2: The run (finding and checking jobs)

A run is a fixed list of steps, always in the same order (`run/graph.py`). Each step saves its progress, so if
the laptop crashes mid-run, the next `/start` continues where it stopped.

### Step 3. Preflight checks

- **File:** `run/steps/preflight.py`
- Before doing any work, the run checks: is the emergency stop (kill switch) on? Is your profile confirmed?
  How many jobs-API calls are left today?
- If your profile has no confirmed items, the run stops here, because it can't judge fit without knowing you.

### Step 4. Jobs left over from earlier runs go first

- **Files:** `run/steps/serve_queue.py`, `sources/queue_rank.py`
- Good jobs that didn't get a card last time (for example, because the daily limit was reached) wait in a
  queue. They are handled first, best score first.
- Older jobs slowly lose priority, and anything waiting more than 10 days is dropped.

### Step 5. Finding new jobs

- **File:** `run/steps/fetch.py`
- Each job source remembers where it stopped last time (its *cursor*), so it only brings jobs you haven't
  seen. There are three sources:

1. **Job Finder** (`sources/job_finder.py`, `sources/finder_agent.py`)
   - Once a week (or when you change your target titles), one AI call lists about 15 *related titles* that
     employers use for the same work, for example "AI Engineer" → "ML Engineer", "GenAI Engineer",
     "LLM Engineer" (`sources/role_expander.py`). You can edit them on the Preferences page.
   - Runs 48 web searches per run (setting `lead_searches_per_run`) using OpenAI's web search, in batches of
     8, for example `site:jobs.lever.co AI Engineer Hyderabad` or
     `linkedin junior Machine Learning Engineer job Bengaluru`.
   - The searches rotate through all your titles (target + related), the sites LinkedIn, Lever, Ashby and
     Greenhouse, your cities, and "junior / entry level" wording. Each run continues where the last one
     stopped, so you get fresh results instead of repeats.
   - From the search results it keeps only pages that are **one specific job**. Pages that list many jobs
     ("500+ AI jobs in Chennai") are skipped.
   - LinkedIn pages are only read from the search result titles. JobPilot never opens or scrapes LinkedIn.
2. **Jobs API** (`sources/jobs_api.py`)
   - Searches Google for Jobs through a service called JSearch. The free plan allows 6 calls a day.
3. **Jobs you share** (`sources/shared_links.py`)
   - Paste a job link (and the job description if you have it) into Telegram, and it goes through the same
     checks as any other job.

### Step 6. Removing duplicates

- **File:** `sources/dedup.py`
- The same job often appears on several sites. JobPilot spots repeats in three ways: the same job ID on a job
  board, the same company + title + city, or nearly identical job description text.
- A repeat is recorded as "seen again" but never becomes a second card.

### Step 7. Quick filters (free, no AI)

- **Files:** `run/steps/screen.py`, `matching/hard_filters.py`
- Jobs are removed only if they are at a company you blocked or have a title you said to avoid.
- Location and experience never remove a job. Location decides the order instead: jobs in your cities
  first, then remote jobs, then everywhere else (`sources/queue_rank.py` → `location_rank()`).
- Every removed job keeps a short reason, which you can see on the dashboard.

### Step 8. A first, rough match

- **File:** `matching/embed_score.py`
- Each remaining job's text is compared with your profile using *embeddings* (a way of turning text into
  numbers so that similar meanings are close together).
- Every job goes on to the next steps, in order: location group first, then this rough match.

---

## Part 3: Scoring each job (one job at a time)

From here, jobs are handled one by one, best first (`run/steps/process_job.py`, `run/pipeline.py`). After
each job, the run checks whether it should stop (`run/stop_rules.py`): card limit reached, `/stop` sent, too
many errors, or nothing left.

### Step 9. Getting the full job description

- **Files:** `sources/jd_enrich.py`, `sources/enrich_lookup.py`
- Search results often contain only a short snippet. JobPilot tries free ways first: the job's own link on
  Lever, Greenhouse or Ashby read through their public APIs, the company's job page found by name, then the
  jobs API. Only if those fail does it ask the Job Finder to search for the full description (at most 60
  times a run, setting `max_jd_lookups_per_run`).
- Each job records where its description came from, or "JD not found" (`jobs.jd_lookup`). A job without one
  is scored on the snippet and the card says "JD not found".

### Step 10. Reading the requirements (one AI call)

- **File:** `matching/extract.py`
- The AI reads the job description once and lists: the technologies (named tools), skills (abilities),
  concepts and project types it asks for, the nice-to-haves, the domain, and (shown only, not scored) the years
  and seniority asked.
- The answer is saved, so the same job description is never paid for twice.

### Step 11. Calculating the score (no AI, plain code)

- **File:** `matching/compute.py`
- The match score (0-100) is calculated by code, so the same job and the same profile always give the same
  number:

| Part | Points | How it's measured |
| --- | --- | --- |
| Technologies | 30 | How many of the named tools (Python, PyTorch, AWS…) you have |
| Skills | 20 | How many of the abilities asked (model evaluation, API design…) your profile shows |
| Concepts | 10 | How many of the ideas asked (transformers, distributed systems…) your profile shows |
| Domain | 10 | How similar the job's area is to your projects and roles |
| Projects | 20 | How similar the things the job builds are to what you have built |
| Nice-to-have | 10 | How many of them you have |

- Experience and the job title are **not** scored (they were removed by your request). The weights live in
  `config/scoring.yaml`; after changing them, restart the backend and run `make rescore`.
- A technology counts only if it is really yours: in your skills list, or named in a confirmed project or
  role. A skill or concept can also earn **partial credit** when something in your profile means nearly the
  same ("model evaluation" vs a project where you evaluated models).
- If a job lists no technologies, skills or concepts at all in one of those three parts, its points go to the
  parts it does list. A search snippet is the exception: it says too little, so an unused part earns half its
  points instead.
- **Why you fit:** code writes a short explanation for every job: your strongest matches, with where your
  profile shows them, and what's missing. The card, the dashboard and (later) the HR email reuse it.

### Step 12. Skills to learn

- **Files:** `matching/gap.py`, `matching/gap_resources.py`
- Every missing item gets a point value: what you'd gain by having it. Example: if a job lists 5
  technologies and you lack one, it is worth 30 / 5 = 6 points (less any partial credit already earned).
- The list is ranked by points *and* how quick each one is to learn, so quick wins come first among items
  worth about the same.
- Each missing skill gets 1-2 free learning links, taken only from the curated list in `Data/learning/`. The
  AI never makes up links.
- Each missing skill also gets a *learnability* score (0-1, no AI): how much free material exists for it in
  the knowledge base, and how close it is to a skill you already have. Skills that are quick to learn may be
  added to your tailored resume.

### Step 13. Does it clear your bar?

- If the score is at or above your bar (set in preferences), the job moves on to get a card.
- If not, it is marked "below your bar". It stays visible on the dashboard and in `/digest`, but you get no
  card.

---

## Part 4: Building the job card

**File:** `cards/build.py`

### Step 14. Tailoring your resume (a new LaTeX copy per job)

- **Files:** `resume/tailor.py`, `resume/tex_versions.py`, `resume/tex_editor.py`, `resume/skill_additions.py`
- For each job, JobPilot copies your base resume's LaTeX (`.tex`) into a new version for that job. Your
  original is never edited.
- The **editor** (AI) returns exact "find this text -> replace it with this" edits on the copy's LaTeX. Code
  applies an edit only if every check passes; refused edits go back to the editor with the reason:
  - **Locked:** the header (name, contact), Education and the LaTeX setup can't be changed.
  - **Safe LaTeX:** only commands your resume already uses; nothing that reads files, runs commands or defines
    macros; no new links or sections; braces balanced.
  - **Truth:** every number, skill and title word must be in your confirmed profile facts. A project or job
    may only mention tools its own fact mentions (your skills list doesn't make a project "use" a tool).
    Project and job titles, companies and dates are never renamed or invented. The summary never gives you a
    title or level you don't hold.
  - **Layout:** skills lines and entries stay intact (no nested or repeated lines).
- Missing job skills that are closely related to yours, or quick to learn, may be added to your skills line.

### Step 15. Build, verify, review - until 95

- **Files:** `resume/verifier.py`, `resume/review.py`
- Each round, the **verifier** (plain code) builds the PDF in the sandbox and reads it back: one page, your
  name and every section in order, no broken characters, and every accepted edit really visible. A round
  that fails is thrown away, and the problems go back to the editor.
- The **reviewer** (AI) then reads the PDF text like a recruiter and scores it out of 100 against the job,
  listing fixes the editor can make with your facts, and separately what only *new* facts from you could fix.
- The loop repeats until the score is **95+** (at most 6 rounds; it also stops early if two rounds in a row
  don't improve). The best version is kept. If 95 wasn't reached, the card says so and names what it needs
  from you (for example "a measurable result for the Turing work").
- Every round's LaTeX, score and checks are saved with the version, so any step can be compared or restored.

### Step 16. Making the PDF

- **Files:** `resume/render.py`, `resume/parse_check.py`
- The resume is turned into a PDF using one fixed LaTeX template (`templates/resume/resume.tex.j2`), built in
  a locked-down sandbox with no internet access.
- A final check makes sure a computer can read every section of the PDF back in the right order, the way
  hiring software would.
- If anything fails, your original resume is used instead, so you still get a card.

### Step 17. Saving the card

- The tailored resume, the card and the messages to send are all saved together in one database step. Either
  everything is saved, or nothing is.
- The messages are not sent yet. They are written to the **outbox**, a list of "things to send"
  (`queue/outbox.py`).

---

## Part 5: Sending you the card

### Step 18. The dispatcher delivers

- **File:** `dispatcher/delivery.py`
- The dispatcher is the only part allowed to send anything. It reads the outbox and sends each message to
  Telegram, in order for each card, exactly once.
- If Telegram is down, it waits and tries again, without sending anything twice.

### Step 19. What the card shows

- **Files:** `cards/render.py`, `cards/messages.py`, `resume/preview.py`
- The card is formatted (Telegram HTML) in clear sections:
  - the job title in bold, then the company, the location and how long ago it was posted;
  - the match score and why you fit (your strongest matches and where your profile shows them);
  - the top 3 skills to learn, each with its points, a "quick to learn" mark and a free course link;
  - the job in one line, and a link to the full details on the dashboard.
- Buttons: **Approve resume vN** and **Apply**, then **View resume** and **More…** (I applied, Email HR,
  Prepare, Skip).
- Then comes **page 1 of your tailored resume as a picture**, so you can read it in the chat, followed by the
  PDF itself, with what changed and the review score.

### Step 20. The summary

- **File:** `run/steps/summary.py`
- When the run ends, you get a summary: how many jobs each source found, how many were filtered, below your
  bar or carded, and the cost.

---

## Part 6: Tapping the buttons

**Files:** `bot/callbacks.py`, `cards/transitions.py`

When you tap a button, the button stops spinning straight away (the worker answers Telegram immediately),
and the action is then carried out.

| Button | What happens |
| --- | --- |
| **Approve resume vN** | Marks that exact resume version as the one you'll use |
| **Open apply link** | Opens the job's application page; you apply yourself |
| **More… → I applied** | Records that you applied |
| **More… → Skip** | Hides the job |
| **More… → Send email to HR** | Starts the HR email flow (Part 7) |
| **More… → Prepare** | Sends a link to the interview-prep page |

Every button carries the exact resume version it belongs to. If the resume changed after the card was sent, an
old button is refused and you're shown the newest version, so you never approve something you haven't seen.

A card moves through these states: **new → resume approved → applied → sending → emailed**. It can also end
as **skipped**.

---

## Part 7: Emailing HR

Every card gets an HR email draft automatically, written in the background and kept quietly (no extra
messages). Nothing is ever sent until you tap **Send**.

### Step 21. Finding the address

- **Files:** `hr_email/service.py` → `find_address()`, `verified()`; `sources/finder_agent.py` → `find_contacts()`;
  `hr_email/address_finder.py`
- JobPilot looks in this order: a hiring address in the job post; an address already used for this company;
  then the Job Finder agent searches the web (and reads a few of the company's own pages) for the company's
  published hiring email.
- Code keeps only addresses the agent actually saw, on the company's **own** domain (not a job board or
  another company), and picks a hiring inbox (careers@, jobs@, hr@...) first, else the general inbox (info@,
  contact@, labelled as such). A named person is shown to you but never used unless you choose it.
- If nothing usable is found, the draft says "no hiring email found" and waits: reply to the card with the
  address (or add it on the dashboard).

### Step 22. Writing the email

- **Files:** `hr_email/email_writer.py`, `safety/email_checks.py`
- The AI writes a short email (at most about 130 words) that explains **why you fit this role**, using your
  tailored resume, the "why you fit" points from the score, and your skill notes.
- Before saving, code checks it: every claim must be backed by your confirmed profile, no salary talk, no
  false urgency, no placeholders, not too long. If it fails, the AI gets one more try with the exact problems.

### Step 23. You tap Email HR, then Send

- After you approve the resume and tap **I applied**, **Email HR** shows the ready draft at once (re-attached
  to the exact resume version you approved), with **Send / Edit / Skip**.
- **Files:** `hr_email/drafts.py`, `hr_email/send.py`, `safety/approval_tokens.py`,
  `servers/gmail/token_verify.py`
- When you tap **Send**, your approval is saved first. At sending time, the dispatcher creates a short-lived
  secret "permission slip" from that approval, and the Gmail server refuses to send without it.
- Each job can get only one email. There's no daily limit; "at most one email per company every 30 days"
  is a setting you can turn on (Preferences, or `/prefs company on`).
- If the laptop crashes during sending, JobPilot checks your Gmail "Sent" folder before trying again, so the
  email never goes out twice.
- If an approved email isn't sent within 24 hours, you're asked to confirm again (`queue/reconfirm.py`).
- **Right now "shadow mode" is on:** emails are logged, not actually sent.

---

## Part 8: Talking about a job (Job Chat)

**Files:** `chat/service.py`, `chat/job_resolver.py`, `chat/proposal_service.py`

- Reply to any card (or send `/chat Company`) to talk about that job: "make my resume focus more on
  Python", "why is my score 65?", "help me prepare".
- The bot works out which job you mean: the card you replied to, or a company or title you mention.
- It can make a **new draft** of your resume or email, which you can approve, change again or undo.
- For anything permanent (changing your profile or preferences), it only makes a **proposal**, and nothing
  changes until you tap **Confirm**.
- It can never send an email, apply for you, or approve anything by itself.
- The job description is treated as plain data, so a job post containing hidden instructions can't control
  the bot.

---

## Part 9: Interview preparation

**File:** `prep/service.py`

When you tap **Prepare** (after approving the resume), JobPilot builds a pack for that job, **mostly from what
it has already stored**, and shows how many AI calls it needed:

1. **Skill recap:** for each skill the job asks for that you already have, the simple-English note from the
   knowledge base. A missing note is written once (at most 4 per pack) and reused by every later pack.
2. **Resources (no AI):** courses and videos for the skills your tailored resume added (learn these first),
   then for the missing skills most worth learning (ranked by points and how quick they are to learn).
3. **Interview Q&A in five groups:** each job skill, each of your projects, this job and company, your
   experience, and the HR round. Answers come from the Q&A bank first; one AI call writes only the groups that
   are still missing, and they're saved for next time. Experience and HR answers are shared by every job.
4. **Every answer shows its source:** a book page, a profile fact or a web page, or a clear "unverified -
   check this answer yourself".
5. Coding-practice patterns (plain code) and a study plan. You mark questions **Known** or **Weak**, and weak
   topics shape the next study plan.

### The knowledge base (Qdrant)

Everything JobPilot knows about skills lives in four Qdrant collections:

| Collection | What's in it | Filled by |
| --- | --- | --- |
| `prep_chunks` | Your 32 interview books, split at their headings, with page numbers | `make ingest-corpus` |
| `learning_resources` | The 755 free courses, videos and docs, tagged by skill, level and type | `make ingest-learning` |
| `skill_notes` | One deep, simple-English note per skill you have, written once by AI from the books | `make skill-notes` |
| `qa_bank` | Interview questions and answers, tagged by skill, project and category | `make skill-notes`, each Prepare, `make qa-bank` |

Looking up one skill (its note, resources and Q&A) takes one round trip and no AI call
(`knowledge/lookup.py`). Try it with `make knowledge-check SKILL=python`.

---

## Part 10: The dashboard

**Files:** `web/main.py`, `web/routes/`, `templates/ui/`

A private website (TOTP login, the 6-digit code from your authenticator app) with:

- **Jobs:** every carded job, its score and reasons, the tailored resume and what changed, the review, skills
  to learn, the email, and the full job description.
- **Profile:** confirm the facts taken from your resume. **Only confirmed facts are used anywhere**, so this is
  the most important page for good scores and resumes (`profile/confirm.py`).
- **Preferences:** your bar, cards per day, cities, titles to target or avoid, and blocked companies.
- **Runs:** the history of every `/start`.
- **Prepare:** your interview-prep packs.

---

## Part 11: Background jobs and safety nets

- **Timers** (`worker/scheduler.py`): keep the run alive, send a daily reminder about cards you haven't acted
  on (`cards/reminders.py`), clear old queue entries, and remove personal details from old records.
- **Alerts:** you get a Telegram warning when something is stuck, a service keeps failing, or a run costs much
  more than usual.
- **Kill switch** (`safety/killswitch.py`): `/killswitch on` stops all runs and all email sending at once.
  It also turns on by itself if errors pile up.
- **Every action is recorded**, so any decision, AI call or message can be traced afterwards.

---

## Where settings live

| What | Where |
| --- | --- |
| Secret keys (OpenAI, Telegram, Supabase, Gmail) | `.env` (never in code) |
| Limits, AI models, shadow mode | `config/defaults.yaml` |
| Score weights | `config/scoring.yaml` |
| Your personal preferences | the dashboard, or `/prefs` in Telegram |
| AI instructions (prompts) | `prompts/` |

## Everyday commands

| Command | What it does |
| --- | --- |
| `/start` | Find and card new jobs |
| `/start 3` | Same, at most 3 cards |
| `/status` | What the current run is doing |
| `/stop` | Finish the current job, then stop |
| `/digest` | Jobs below your bar, queued jobs, cards waiting for you |
| `/prefs` | See or change preferences |
| `/chat Company` | Talk about one job |
| `/killswitch on` / `off` | Emergency stop |

---

## Code map: where each step is implemented

All paths are inside `src/jobpilot/`. `file.py` → `function()` is where to start reading.

| Step | What | Code |
| --- | --- | --- |
| 1 | Read Telegram messages, stop the button spinner | `worker/telegram_poller.py` → `run()`, `_acknowledge()` |
| 1 | Save the message and queue it as work | `bot/router.py` → `ingest_update()` |
| 2 | Route a message to a command, button or chat | `bot/router.py` → `handle_event()` |
| 2 | `/start` command | `bot/commands.py` → `start()` |
| 2 | One run at a time | `run/lease.py` → `acquire()` |
| 2 | The run's fixed list of steps | `run/graph.py` → `build_graph()`, `execute_run_request()` |
| 3 | Preflight checks | `run/steps/preflight.py` → `preflight()` |
| 4 | Leftover jobs first | `run/steps/serve_queue.py` → `serve_queue()`; `sources/queue_rank.py` → `ranked()`, `expire()` |
| 5 | Fetch every source | `run/steps/fetch.py` → `fetch()` |
| 5 | Job Finder: the rotating searches | `sources/finder_agent.py` → `lead_query_pool()`, `lead_queries()` |
| 5 | Job Finder: pick single job postings | `sources/finder_agent.py` → `find_leads()`, `linkedin_lead()` |
| 5 | Job Finder as a source (cursor) | `sources/job_finder.py` → `JobFinderSource.fetch()` |
| 5 | Jobs API (JSearch) | `sources/jobs_api.py` → `JobsApiSource.fetch()` |
| 5 | Jobs you share | `sources/shared_links.py` → `find_url()`, `posting_from_share()` |
| 6 | Remove duplicates | `sources/dedup.py` → `ingest()`, `find_existing()` |
| 7 | Quick filters | `run/steps/screen.py` → `screen()`; `matching/hard_filters.py` → `check()` |
| 5 | Related titles (weekly AI call) | `sources/role_expander.py` → `suggest()`, `merge()`; `run/steps/fetch.py` → `refresh_related_titles()` |
| 7 | Location order (not a filter) | `sources/queue_rank.py` → `location_rank()` |
| 8 | Rough first match (orders jobs) | `matching/embed_score.py` → `stage_a()` |
| 9-13 | One job through the pipeline | `run/steps/process_job.py` → `process_job()`; `run/pipeline.py` → `process()` |
| 9-13 | When to stop | `run/stop_rules.py` → `evaluate()` |
| 9 | Full job description | `sources/jd_enrich.py` → `find_full_jd()` |
| 10 | Read the requirements (AI) | `matching/extract.py` → `extract()` |
| 11 | Calculate the score | `matching/compute.py` → `compute()`, `SkillMatcher.match()`, `SkillMatcher.in_experience()` |
| 12 | Skills to learn + points | `matching/gap.py` → `gap()` |
| 12 | Learning links | `matching/gap_resources.py` → `for_skill()` |
| 13 | Queue jobs above the bar | `sources/queue_rank.py` → `enqueue()` |
| 14-17 | Build a card | `cards/build.py` → `build()`, `_tailored()` |
| 14 | Copy the base resume LaTeX for the job | `resume/tex_versions.py` → `base_tex()`, `sections()`, `plain()` |
| 14 | Editor (AI) + every edit checked | `resume/tex_editor.py` → `propose()`, `apply()`, `latex_problem()`, `claim_problem()`, `structure_problem()` |
| 14 | Add related skills | `resume/skill_additions.py` → `eligible()`, `boost()` |
| 15 | The edit → build → verify → review loop | `resume/tailor.py` → `tailor()`, `unchanged()` |
| 15 | Verifier (code) | `resume/verifier.py` → `verify()`, `check_text()` |
| 15 | Resume reviewer (AI) | `resume/review.py` → `review()`, `passes()`, `feedback()` |
| 16 | Build the PDF in the sandbox | `resume/render.py` → `compile_pdf()` |
| 16 | Read the PDF back | `resume/parse_check.py` → `acheck()` |
| 17 | Write to the outbox | `queue/outbox.py` → `telegram_message()`, `enqueue()` |
| 18 | Send everything, once, in order | `dispatcher/delivery.py` → `Dispatcher.deliver_ready()`, `_deliver_one()` |
| 19 | Card text and buttons | `cards/render.py` → `text()`, `buttons()`, `more_buttons()`; `cards/messages.py` → `send_card()`, `send_resume()` |
| 19 | Resume preview image | `resume/preview.py` → `first_page_png()`, `render()` |
| 20 | Run summary | `run/steps/summary.py` → `summary()` |
| Part 6 | Button taps | `bot/callbacks.py` → `handle()`, `_dispatch()` |
| Part 6 | Approve / applied / skip | `cards/transitions.py` → `approve_resume()`, `applied()`, `skip()` |
| 21 | Automatic draft for every card | `cards/build.py` (queues it); `hr_email/service.py` → `compose(auto=True)` |
| 21 | Find the HR address | `hr_email/service.py` → `find_address()`, `verified()`, `choose()`; `sources/finder_agent.py` → `find_contacts()`; `hr_email/address_finder.py` → `find()`, `classify()` |
| 22 | Write the email (AI) | `hr_email/email_writer.py` → `write()` |
| 22 | Check the email | `safety/email_checks.py` → `check()`, `complete_citations()` |
| 23 | Save the draft; show it with Send / Edit / Skip | `hr_email/drafts.py` → `store()`, `release()` |
| 23 | You tap Send | `hr_email/send.py` → `approve_and_queue()` |
| 23 | The permission slip | `safety/approval_tokens.py` → `mint()`, `verify()`; `servers/gmail/token_verify.py` → `check()` |
| 23 | 24-hour re-confirm | `queue/reconfirm.py` → `sweep()`, `expire_send()` |
| Part 8 | Job Chat | `chat/service.py` → `handle()` |
| Part 8 | Which job you mean | `chat/job_resolver.py` → `resolve()` |
| Part 8 | Proposals (Confirm / Cancel) | `chat/proposal_service.py` → `create()`, `decide()` |
| Part 9 | Interview prep pack (storage first) | `prep/service.py` → `generate()`, `job_skill_ids()` |
| Part 9 | Write only the missing Q&A groups (AI) | `prep/qa_generator.py` → `generate_missing()`; `prep/qa_bank.py` → `from_pack()`, `group_source()` |
| Part 9 | Knowledge base lookup (no AI) | `knowledge/lookup.py` → `for_skill()`, `material()` |
| Part 9 | Learning resources in Qdrant | `knowledge/learning.py` → `ingest()`, `link_ok()` |
| Part 9 | Skill notes (AI, once per skill) | `prep/skill_notes.py` → `write()` |
| Part 9 | Q&A bank | `prep/qa_bank.py` → `add()`, `from_prep_set()`, `search()` |
| 12 | Learnability of a missing skill | `matching/gap.py` → `learnability()`, `with_learnability()` |
| Part 10 | Dashboard app and pages | `web/main.py`; `web/routes/*.py` |
| Part 10 | Confirm profile facts | `profile/confirm.py` → `confirm_all()`, `set_status()` |
| Part 11 | Timers | `worker/scheduler.py` → `run()`, `tick()`, `daily()` |
| Part 11 | Daily reminder and `/digest` | `cards/reminders.py` → `send_daily_reminder()`, `digest()` |
| Part 11 | Kill switch | `safety/killswitch.py` → `engage()`, `release()`, `is_engaged()` |
