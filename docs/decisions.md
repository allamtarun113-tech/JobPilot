# Decisions

Why the code is the way it is, newest first. Numbers match the references in code reviews and notes.

## 41. Skills from the job only; second try for bullets; objective from facts; no dollar amounts shown (2026-10-09)
**Context (user, after run 19).** Listing every confirmed skill made the resume ugly; refused rewordings left awkward bullets; the AI's objective was refused and the old generic one stayed; "Generative AI" in a job title wasn't used; dollar amounts should not be shown.
**Skills (overrides decision 39 for the skills section only).** With a job description, the section lists only your confirmed skills the job asks for: requirements (technologies, skills, concepts, must/nice), skills the title or description names, and skills of a taxonomy category a broad requirement asks for ("AI/ML technologies" -> ml). Fewer than 3 matches keeps every skill. Each skill appears once, on the line of its kind (`LABEL_FIT`: Python under Programming Languages); unplaced skills join a matching line ("Vector Database" -> "Databases"); a skill that only repeats a label is dropped. Required skills first. Experience, projects, bullets and activities are still never removed.
**Second try.** Bullets whose rewording was refused go back once in a focused call (`resume/fix_bullets_v1`, schema `BulletFixes`) with the refused wording and the reason; the plan is recomposed with the fixes (still checked by `safety/fidelity.py`).
**Objective.** When the model's objective is empty or refused, code writes one from facts: current role at organization + up to 5 job-relevant skills you have, the most job-relevant project that reads "Developed/Built a/an ...", and your degree. Still checked by `summary_problem` (entry fields and proper skill names now back it).
**Job title.** Its words join the job description for skill names (resume and email).
**No dollar amounts** in the Telegram run summary, `/status`, the dashboard metrics or the Runs table. Costs are still recorded (`llm_usage`, `runs.cost_usd`) for the budget checks. Prompt `resume/compose_v5`.

## 40. A Qdrant upload can't fail a run (2026-10-09)
**Context.** Run 18 found 103 new jobs, then failed in screening: stage A sent all 103 job vectors to Qdrant Cloud in one request and it hit `httpx.WriteTimeout` (client default 5 s). Nothing reads the `jobs` collection back; the stage A scores are computed locally.
**Decision.** `matching.embed_score.stage_a` stores job vectors best-effort (a failure is logged, scoring continues). `VectorStore.upsert` sends batches of 64, retries a network error up to 3 times with a short wait, and the Qdrant Cloud client uses `vectors.timeout_seconds: 30`. The 103 jobs stayed `new` and are screened by the next run (no new searches needed). Tests: `tests/unit/test_vector_resilience.py`.

## 39. Never remove anything; polish by code; required skills first (2026-10-09)
**Context (user).** "You should not remove anything from the resume - only reorder, or add new from the profile. No spelling mistakes, proper formatting and grammar, proper skills; required skills first, then the rest."
**Nothing removed.** Page trimming is gone (`compose.trim` deleted). Every role, project, detail, skill line, skill and activity stays; projects the plan leaves out follow, ordered by how many of the job's required skills they name. Over `resume.max_pages` (2) is only a note in the verify report, never a failure or a cut. Every confirmed skill is shown: ones the plan doesn't place go on lines by taxonomy category (AI and LLM Tools / Backend and Databases / Cloud, DevOps and Tools / Additional Skills).
**Polish by code** (`resume/polish.py`, offline `pyspellchecker`): misspelled words fixed when the fix is within two letters and the word isn't a taxonomy term, a profile name, in `resume.known_words`, an acronym, a mixed-case or mid-sentence capitalised name; first letter capitalised; sentence bullets end with a full stop (activities get none); month names and "Present" in title case; no full stop after an entry title; skills labels in title case. Run on the pool (so the composer sees correct facts) and on the final resume.
**Proper skill names** (`catalog.display_skill`): your capitals are kept unless the official name has inner capitals (NumPy, LangChain, JavaScript) or yours is all lower/upper case.
**Required skills first:** each skills line is ordered required (requirements' technologies, skills, concepts, must_skills) -> nice-to-have -> named in the JD -> rest; lines holding required skills come first. Prompt `resume/compose_v4` also asks the model to fix every grammar error and place every confirmed skill.

## 38. Two-page resume, two-line bullets, nothing cut; emails show a project and skills (2026-10-09)
**Context (user).** The first live email named only company work; tailored resumes dropped skills lines and activities to fit one page; some bullets ran to 4-5 lines.
**Resume.** `resume.max_pages: 2`. Trimming (`compose.trim`) never cuts skills lines or activities (only extra projects, then project bullets, then role bullets beyond two). A base skills line always keeps every skill, even one another line also lists. `resume.max_bullet_chars: 210` (about two lines): `pool.split_long` splits a longer fact into bullets of whole sentences (ids `p48`, `p48.2`, ...; same entry, same evidence, no rewording); a single sentence still too long is marked `too_long` for the composer to shorten (min_keep 0.4); any rewording over the limit is refused. Prompt `resume/compose_v3`.
**Email.** Prompt `hr_email/email_writer_v6` asks for one experience fact, one project fact and one more. If no project sentence survives, code adds the project fact that names most of the job's skills ("In my JobPilot project, I built ..."). Code adds "Relevant skills: ..." - confirmed skills the job description asks for (taxonomy match, up to 8, cited). Fit sentences together are capped at 75 words so the email stays under 150. Skill display names moved to `reference/catalog.display_skill` (shared by resume and email).

## 37. GPT-5 nano for every task (2026-10-08) - reverted 2026-10-09
**Context.** The user asked to move from gpt-4o-mini to gpt-5-nano. OpenAI's model page (checked 2026-10-08): `gpt-5-nano` (snapshot `gpt-5-nano-2025-08-07`), $0.05 / $0.005 cached / $0.40 per 1M tokens (input / cached input / output), 400k context, 128k max output, structured outputs and the Responses `web_search` tool supported. It is a reasoning model: hidden reasoning tokens are billed as output and count against `max_output_tokens`; web search doesn't work at "minimal" effort.
**Decision.** `config/defaults.yaml`: every task -> `gpt-5-nano`; `llm.reasoning_effort: low`, `medium` for `resume_composer` and `email_writer`; `llm.reasoning_headroom_tokens: 8000` is added to every reasoning call's output limit (`LLMCfg.effort_for`, `llm/client.py`). The web search server sends the effort too and turns "minimal" into "low". A non-reasoning model (gpt-4o-mini) still gets no `reasoning` field, so switching back is a config change. Pricing rows for both models are kept.
**Reverted (2026-10-09, user decision):** every task is back on `gpt-4o-mini`. Run 16 on gpt-5-nano took 24 min with no card, found 12 leads from 48 searches (gpt-4o-mini: ~93), returned a malformed agent step and a stray CJK character in a resume bullet. The reasoning settings stay in config and code but apply only to gpt-5 / o-series models, so gpt-4o-mini requests are unchanged.
**Consequences (when it was on).** Cheaper per token, but each call also pays for reasoning tokens and is slower (decision 21 measured gpt-5-mini web search at ~17 s vs ~4 s). Cached requirement extractions are keyed by model id, so jobs are re-extracted once on the next rescore. Live quality is not yet measured (no OpenAI calls were made for this change); watch the first runs' cost and latency in the Runs tab.

## 36. Resume v3 and email v4: code builds them from your facts; the model only picks, orders and rewords (2026-10-08)
**Context.** Live resumes from the v2 LaTeX editor (decision 31) got worse: a new project (JobPilot) was pushed under "Bus Transportation" because entries couldn't be added; Turing's four confirmed details were squeezed into one line; rewrites inflated facts ("Learned about BEML projects" -> "Contributed to the design and manufacturing of vehicles", "Worked in Gen AI projects" -> "Led ... ensuring high-quality outputs", Flask -> FastAPI, "climate modeling"); the objective repeated itself and called you a "Generative AI Developer". The reviewer's push for "metrics and impact" fed the inflation. HR emails were generic, asked HR odd questions and stated unbacked things.
**Decision (resume v3).** `resume/pool.py` collects every confirmed experience and project with each detail as its own bullet (new profile entries included). The composer (`resume/compose_v2`, one call per round) returns a `ResumePlan`: entry/bullet ids in order, optional rewording of one bullet, a summary and skills lines. `resume/compose.py` applies it: all roles and all their details stay (forgotten bullets come back; nothing merges or moves between entries); projects follow the plan, unlisted ones go last; skills are confirmed only. A rewording lands only if `safety/fidelity.py` passes it against its one source fact: no new content words (only word forms, typo fixes and plain verbs), the lead verb keeps what you did, "learned / assisted" survive, planned stays planned, most detail kept, numbers/skills/titles backed; else the original wording stays. The summary is refused for titles, job nouns you don't hold, puffery, impact claims, numbers or unconfirmed skills. Code renders the one fixed template (rule 8 restored: no model-written LaTeX) and trims to one page lowest-priority first (extra projects, activities, skills lines, project bullets; experience last). Reviewer `resume/review_v3` checks truth first and never asks for invented metrics; max 3 rounds, stop when a round is no better. Unconfirmed "addable" skills (decision 17) are no longer put on resumes. Versions store the composed doc (`ResumeDoc.composed`) and its LaTeX; Job Chat edits re-run the composer with your request. Removed: `resume/tex_editor.py`, `models/resume_tex.py`, `resume/tex_edit_v1`.
**Decision (email v4).** Code writes the subject ("Application for <role> - <name>"), greeting, opening ("I have applied for ... and have attached my resume"), closing and signature with your contacts. The model (`hr_email/email_writer_v5`) writes only 2-3 sentences, each about one experience/project fact, checked by the same fidelity rules (a refused one is retried once, then falls back to the fact itself or is dropped). Email checks ignore the signature block and refuse puffery.
**The job's language (same day, user request).** A bullet or email sentence may use the job description's own name for a skill the fact already names, when the skills taxonomy says it is the same skill (`fidelity.job_terms` / `same_skill_words`): "Gen AI" -> "Generative AI", "RAG" -> "retrieval-augmented generation", "Qdrant" -> "vector databases (Qdrant)", "LLM as a judge" -> "LLM evaluation". A job skill the fact doesn't name (Kubernetes, MLOps) is still refused.
**Tested without OpenAI:** scripted model answers replaying the live mistakes against the real profile (all refused), plus unit, integration and eval suites (`tests/unit/test_resume_v3.py`, `tests/unit/test_hr_email.py`).

## 35. Structured profile: experiences and projects hold their own details (2026-10-07)
**Context.** The profile page listed every item by kind, so all resume bullets sat in one "Achievements" list with no sign of which job or project they came from, and the facts sent to the tailor, email writer, Job Chat and Prepare lost that context too.
**Decision.** No migration: an experience (`role`) or project item keeps its fields in `attrs` (`ENTRY_FIELDS` in `models/profile.py`: title, organization, dates, location, type, team, tech, about / name, your role, context, link). Details (`achievement`, `metric`) point at it with `attrs.parent_id` (the importer's `attrs.parent` key still resolves). The page (`profile/structure.py`) shows one card per experience and project with its details, an add-detail form, an edit-fields form and "Belongs to" moves; entries and details typed there are confirmed (`source = manual`).
**Downstream.** Every feature reads facts through `fact_texts()`: an entry carries its fields, a detail carries "[its experience/project]". Tools listed on an entry count as skills backed by that entry in `matching/profile_view.py`; a role whose type says internship is left out of years. Notes use `profile/extract_items_v2` with a `belongs_to` field that only accepts a listed name.

## 34. Prep v2: a pack built mostly from storage (2026-10-07)

- **Pack** (`prep/service.py`, `prep_sets.pack`, migration 14):
  - **Recap:** stored skill notes for the job's skills you have; a missing note is written by one call, at most `MAX_NEW_NOTES` (4) per pack.
  - **Resources (no model call):** for the skills the approved resume added (`add_skill` operations) and the top 5 ranked missing skills.
  - **Stats:** AI calls, how many Q&A were reused vs generated.
- **Q&A groups:** skill, project, job, experience, hr. They're read from the Q&A bank first:
  - skill: by `skill_ids`;
  - the others by `source`: `project:<id>`, `experience`, `hr`, `prep:<job id>`.
  One call (`prep/qa_generator_v3`, `generate_missing`) writes only the missing groups, grounded in book sections for those skills, your profile and (for the job group) Prep Research notes. The new Q&A is saved with checked citations and readable `source_labels` (`qa_bank.from_pack`). Experience and HR answers are shared across jobs, so later packs usually need no model call.
- **Sources:** every question carries source labels, or "no source found - check this answer yourself". Unknown citations are dropped, and an answer is verified only with a known source.
- **Removed:** the topic planner (`prep/planner.py`, `prep/planner_v2`) and `prep/grounding.py`; the citation checks now live in `qa_bank.from_pack`. The prep eval runner checks that boundary. Known / Weak marks and the study plan are unchanged.
- **Fixed:** `knowledge.lookup.for_skill` skips a read whose count is 0 (Qdrant refuses a limit of 0).

## 33. HR email v2: automatic drafts, agent-found addresses (2026-10-07)

- **Automatic drafts (D3):** each card build queues an `email_draft` work item (`auto: true`). The draft is stored with status `prepared` (address found) or `needs_address`. There is no Gmail draft and no message, so the chat isn't flooded. **Email HR** (after approval + I applied) releases it at once (`drafts.release`): the Gmail draft is created through the outbox with the exact approved resume, then the preview with Send / Edit / Skip. If the approved resume or the address differs, the same text becomes a new version (no new model call). Send refuses a draft you haven't been shown. Nothing is sent without your Send tap (unchanged exactly-once flow).
- **Address agent:** a new task of the existing Job Finder agent (`find_contacts`, prompt `agents/job_finder_v3`). It is not a fifth agent. It has its own usage budget per job (`contacts:<job id>`, freed afterwards) and returns only addresses literally seen in observed results or fetched pages. Code (`service.verified`) keeps those on the company's own domain and ranks hiring inbox > general inbox; a named person is never used by default. Order: the job post, then an address already used for this company (`emails.known_address`), then the agent.
- **Writer v3** (`hr_email/email_writer_v3`): explains why you fit this role from the tailored resume (the LaTeX copy), `scores.fit` and short skill-note summaries (knowledge base; wording only). The same deterministic checks and one retry.
- **Limits (D4):** no daily email limit (`caps.emails_per_day: null`; setting a number brings it back); one email per job always; the one-per-company-per-30-days rule is a preference, `company_rule`, off by default (dashboard checkbox, `/prefs company on|off`). This overrides the design's guardrail table.

## 32. Card v2: formatted card and resume preview (2026-10-07)

- **Formatted card** (`cards/render.py`): Telegram HTML with sections for the role and company, location and posted date, match and why you fit (from `scores.fit`), top 3 skills to learn with points, a "quick to learn" mark and one catalog link each, the JD in one line, and the dashboard link. All job data is HTML-escaped. Cards are sent and edited with `parse_mode: HTML`, the only parse mode the Telegram server accepts.
- **Resume preview** (`resume/preview.py`): page 1 of the PDF rendered to PNG with pypdfium2 (backend group; no system tools, works in the cloud container) and stored next to the PDF (`.png`). The card partition sends it as a photo before the PDF.
- **New Telegram tool `send_photo`**: server, scope config and client wrapper together. It sends only stored `resumes/*.png`. A missing preview (versions from before this change) is skipped (`DeliverySkipped`), so it never blocks the card's partition.
- **Buttons**: Approve resume vN + Apply, then View resume + More…; the More menu has I applied, Email HR, Prepare, Skip and Back. Callback data is unchanged.

## 31. Resume v2: a LaTeX copy per job, edited until 95 (2026-10-07)

- **User decision D2 overrides code rule 8** ("the LLM never writes LaTeX"): for each job, the base resume's `.tex` (the fixed template filled with the base resume, `tex_versions.base_tex`) is copied, and the editor agent edits only that copy. It is stored in `resume_versions.tex` (migration 13), and the PDF is built from it. The original is never touched; `doc` stays the base resume.
- **Editor** (`resume/tex_editor.py`, prompt `resume/tex_edit_v1`): exact find -> replace edits; `find` must occur once in the named section, with spacing differences tolerated. Code refuses an edit that:
  - touches the preamble, header or Education;
  - uses a command the base body doesn't use, or any file/shell/macro/catcode command;
  - adds a link or section, or has unbalanced braces or environments, or unescaped special characters;
  - claims a number, skill or title word that the cited facts or the replaced text don't back.
  Stricter truth rules after the first live trial: in projects, experience and activities only the cited project/role facts back a skill (skills-list items and addable skills back only the skills lines and the summary); entry titles, companies and dates can't be renamed or invented; the summary adds no title words; layout commands stay top-level and no skills label repeats.
- **Verifier** (`resume/verifier.py`) is code, not a model: sandbox build, page limit, name and sections in order, no broken glyphs, every edit visible. A failed round is discarded and its problems go back to the editor.
- **Reviewer** (prompt `resume/review_v2`) scores the PDF text against the JD (D1). It lists fixable issues separately from `needs_facts`, which only new profile facts could close.
- **Loop** (`resume/tailor.py`): edit -> build -> verify -> review until 95 (`resume.review_pass_score`), at most 6 rounds, stopping after two non-improving rounds. The best verified round is kept; every round is stored in `resume_versions.rounds`. The card says when 95 wasn't reached, and what is needed.
- **Job Chat** edits on a v2 version apply the user's request through the same editor and verifier (one round); undo restores the previous copy.
- `make resume-trial` runs the loop on real jobs without creating cards, saving PDFs, LaTeX and per-round details to `Data/user/trials/`.

## 30. Rubric 4: weights for an early-career AI/ML search (2026-10-07)

- **Weights:** technologies 30 (was 25), skills 20, concepts 10 (was 15), domain 10 (was 15), projects 20 (was 15), nice 10. Named tools are the most verifiable signal and are what screeners check; for an early-career candidate, having built something similar predicts fit better than the business domain; concepts are listed loosely and only match by meaning. The three required-item parts still total 60, so the hand-over rule is unchanged.
- **Measured on the same 20 jobs:** scores moved -1.3 to +3.7 and the order barely changed. The weights are not what keeps scores low; missing tools (RAG, Docker, MLOps, TypeScript) and a partly confirmed profile are.
- Change weights only in `config/scoring.yaml` (raise `rubric_version`), restart the backend, then run `make rescore`.

## 29. Matching v2: rubric 3 (2026-10-07)

- **Extraction v2** (`prompts/scoring/extract_requirements_v2.md`): technologies, skills, concepts, project types, nice-to-haves, domain; years and seniority are kept for display only. `must_skills` is derived in code (technologies + skills) and left out of the model's schema (`x-derived`, `llm/schema.py`), so tailoring, prep, cards and evals read it unchanged. Rubric v1 rows (must_skills only) score their must_skills as technologies.
- **Score** (`matching/compute.py`, `scoring.yaml` rubric 3): technologies 25, skills 20, concepts 15, domain 15, projects 15, nice 10. No experience and no role direction. A full JD that uses none of one of the three required-item parts hands that weight to the parts it uses; a snippet doesn't (an unused part earns half its own weight), so one vague phrase can't carry 60 points.
- **Partial credit by meaning** for skills and concepts only: credit = scale(best similarity to any confirmed skill, project or role; 0.72 -> 0, 0.88 -> 1). The thresholds were measured on the real profile with bge-small: unrelated phrases reached 0.60-0.71. Technologies need a real match. The unearned part stays in the gap, so score + gap = 100 still holds.
- **Fit** (`compute.explain`, stored in `scores.fit`, migration 12): full matches with where the profile shows them, the closest similar work, and the top missing items. Code only, so the card, the dashboard and later the email agree.
- **Skills to learn** (`gap.rank`): points x (0.6 + 0.4 x learnability).
- **Effect:** on the 20 re-scored jobs, v3 averages about 16 points below v1 (range -0.5 to 34), because v1 gave nearly free experience and role points. `make rescore` re-scores queued and below-bar jobs. Also fixed: a job re-scored below the bar now leaves the queue.

## 28. Knowledge base in Qdrant (2026-10-07)

- **Four collections:** `prep_chunks` (book sections, `make ingest-corpus`), `learning_resources` (all 755 catalog resources, tagged with every skill they serve and their rank per skill, `make ingest-learning`), `skill_notes` (one note per skill) and `qa_bank` (interview Q&A, reused across jobs). Filtered fields have keyword indexes (`integrations/vectors.py` `KEYWORD_INDEXES`).
- **Shared package `knowledge/`:** the learning catalog (`learning.py`, moved out of `matching/gap_resources.py`) and the no-model skill lookup (`lookup.for_skill`: note + resources + Q&A from three payload-filtered reads in parallel, no embedding). Any feature may read it.
- **Skill notes** (`prep/skill_notes.py`, prompt `prep/skill_note_v1`): one gpt-4o-mini call per skill, grounded in that skill's book sections (citations checked in code; unknown ids dropped). The same call writes 5-8 Q&A for the bank. Written once (`make skill-notes`), reused after that. This is a single call, not a new agent.
- **Q&A bank** (`prep/qa_bank.py`): tagged by skill_ids, project_ids, category, source, company/title. Each prep set adds its questions (`make qa-bank` backfills). The same source + question is the same point.
- **Notes and Q&A live only in Qdrant.** They aren't rebuildable from Postgres; rewriting them costs about $0.002 per skill. Accepted to avoid a new table; a backup export can come later.
- **Learnability** (`matching/gap.py`, settings in `scoring.yaml` `learnability`): material = resources + book sections tagged with the skill; closeness = the best similarity to one of your confirmed skills (now indexed in `profile_items` as `kind: skill`), or a taxonomy link. Each gap item stores `learnability`, `easy` and `learn_reason`; "quick to learn" for resume skill additions uses it, and falls back to taxonomy depth for older scores. If Qdrant is down, the score is unchanged and learnability is skipped.
- **Dashboard stays slim:** the catalog code imports the vector client lazily, and a test fails if the dashboard loads qdrant_client, fastembed, openai, langgraph or pypdf.

## 27. Wider job search: related titles, search budget, location order, JD for every job (2026-10-07)

- **Related titles:** one model call (`sources/role_expander.py`, prompt `sources/role_expansion_v1`) lists ~15 market titles near your targets, with a reason each. It is a single call, not a fifth agent. It runs at the start of fetch when the list is a week old or your target titles changed. The list lives in preferences (`related_titles`), is editable on the dashboard, and titles you delete (`related_titles_removed`) or avoid are never suggested again.
- **Search budget:** `agents.job_finder.lead_searches_per_run` (48) replaces the fixed 8, run in batches of 8 so the model sees each batch whole. Titles vary fastest, then sites (LinkedIn first), then places; runs keep rotating. One failed batch no longer loses the others.
- **No location or experience filters:** location orders jobs (your cities, remote, elsewhere; `queue_rank.location_rank`) in screening and in the queue. Experience is left to the score. Stage A no longer cuts to the top 20; every survivor gets a JD lookup and a score.
- **JD for every job:** lead links on Lever, Greenhouse and Ashby are read through their public APIs first (free, fixed hosts). The agent runs only if that fails, capped by `max_jd_lookups_per_run` (60). `jobs.jd_lookup` (migration 11) records `found:<where>` or `not_found`, and cards and the dashboard say "JD not found". Requirements stay per job through `scores.requirements_id`.
- **Cost:** about 48 lead searches plus about 2 per agent lookup, at roughly $0.01 per OpenAI web search, so about $1-2 per `/start` at full use. Lower `lead_searches_per_run` / `max_jd_lookups_per_run` to spend less.

## 26. Faster dashboard, cloud-ready backend (2026-10-07)

- **Dashboard:** it no longer imports the AI, vector and PDF libraries. Card messages moved to `cards/messages.py`, prep marks to `prep/marks.py`, and the OpenAI SDK loads on first use. Backend-only packages are in the `backend` dependency group, so Vercel installs only the dashboard's. Measured: cold start 9.7 s -> about 1 s.
- **Backend:** `deploy/Dockerfile` packages the worker + dispatcher with a pinned static Tectonic and the embedding model. In the container, PDFs build in `container` mode (`--only-cached --untrusted`, chosen when `JOBPILOT_IN_CONTAINER` is set). It stays on the laptop for now (no spending); how to move it is in `docs/cloud-backend.md`.
- **Local installs** use `uv sync --all-groups` (`make setup`).

## 25. Project layout (2026-10-06)

- **Decision:** one Python package, `src/jobpilot/`, organised by what each part does. The three processes live in it (`web/`, `worker/`, `dispatcher/`) next to the small Telegram, Gmail and web servers (`servers/`). Small packages were merged: `security` + `guardrails` -> `safety`, `filtering` + `scoring` + `learning` -> `matching`, `discovery` + the Job Finder -> `sources`, `domain` -> `models`, `observability` -> `monitoring`, `telegram` -> `bot`, `outreach` -> `hr_email`, Job Chat -> `chat`. `deploy/` holds the supervisor configs, the launchd job and the LaTeX sandbox.
- **Import rules** (`tests/unit/test_layers.py`): shared code never imports a feature; features (sources, matching, resume, hr_email, profile, prep) never import each other; run, bot, cards and chat coordinate features; nothing imports the process entry points.
- **Older entries below** describe the layout as it was at the time.

## 24. Alert emails and salary checks removed from the code (2026-10-06)

- **Decision:** delete the alert-email source (board parsers, Gmail inbox search and read tools, parse-yield alerts, fixtures, `make alerts-check`) and the salary feature (floor filter, parking, jobs-API estimates, the Salary Research agent, salary preferences, CTC on cards). Supersedes decision 16, 18 and the alert parts of 6 and 22.
- **Kept:** Gmail for HR drafts, Sent lookup and sends (dispatcher only); the "no salary talk" email check; the unused `jobs.salary_*` columns (dropping them would need a migration on the live database).
- **Also removed:** dead functions found by a vulture scan, stale audit reports, runtime leftovers in `var/`, `.env.local`. A full code backup was taken first: `Data/user/backups/code-before-cleanup-20261006T201446.tar.gz`.
- **Tests:** end-to-end runs now use a fixture job source (`tests/conftest.py` `FixtureSource`) instead of alert-email fixtures.

## 23. No daily token limit (2026-10-06)

- **Context:** run #20 stopped at preflight with "the daily token budget is used up" (613k of 600k tokens, $0.11 that day on gpt-4o-mini), while the user is scaling to 20-30 jobs a day.
- **Decision:** `budgets.tokens_per_day: null` = no daily limit (`safety/caps.py` `token_budget_left` returns `UNLIMITED`); no budget stop rule, no budget kill switch, no 80% alert. Setting a number brings all three back.
- **Still in place:** per-agent caps per run (searches, pages, tokens), the Job Chat daily budget, the alert when one run costs over 1.5x its estimate, and the jobs-API quota.

## 22. No alert emails; rotating searches that include LinkedIn (2026-10-06)

- **Context:** the user can't get LinkedIn / Naukri alert emails, so the Gmail alert source never produced a job; Job Finder repeated the same 6 searches and found 0-5 new jobs a run, mostly senior.
- **Decision:** `sources.alert_emails: false` (code kept; Gmail stays for HR drafts). Job Finder rotates through a pool of searches - your titles x cities (+ remote India, India) x level (any, junior, entry level) x {Lever, Ashby, Greenhouse, LinkedIn} - 8 per run, continuing where the last run stopped (`query_offset` in its cursor).
- **LinkedIn:** a plain-language query ("linkedin <title> job <city> posted this week") returns `/jobs/view/` pages; their result titles are turned into leads by code (`linkedin_lead`). LinkedIn pages are never fetched. Naukri via search gave stale, unrelated results, so Naukri should come through the jobs API (Google for Jobs).
- **Measured:** one live batch went from 0-10 leads to 28 (13 LinkedIn, several junior). Long snippets had pushed later searches out of the model's 30k-character window; snippets are now cut to 240 characters.

## 21. OpenAI web search replaces Tavily (2026-10-06)

- **Decision:** the web MCP's `search` tool calls the OpenAI Responses API with the built-in `web_search` tool (`src/jobpilot/servers/web/search.py` `OpenAIWebSearch`, model `models.web_search` = gpt-4o-mini), using `OPENAI_API_KEY`; `SEARCH_API_KEY`, Tavily and Brave are removed. Fetch is unchanged (SSRF guard + deny-list).
- **Only real URLs:** results come from the tool's `url_citation` annotations and `web_search_call` sources; model-written text is used only as a snippet. OpenAI's `utm_source=openai` is stripped so seen-URL cursors and dedup match.
- **Measured 2026-10-06:** ~4 s and ~8.5k input tokens per search; `site:` in the query works. Domain `filters` are refused for gpt-4o-mini and gpt-4.1-mini (gpt-5-mini accepts them but took 17 s).
- **Exception noted:** this is a second caller of the model API besides `llm/client.py`; its token use is not yet in `llm_usage` or the run budget.

## 20. Instant tap acknowledgement, fewer round trips, HR email from Telegram (2026-10-06)

- **Context:** Supabase is in Sydney (~0.3 s per round trip from India, ~0.9 s per transaction, ~5.8 s per new connection); taps took 4-12 s, the pool reconnected every 2 minutes, and long-poll timeouts backed off up to 60 s. No HR email was ever drafted: the company-domain filter needed the full company name in the domain, and Telegram offered no way to give an address.
- **Decision (exception to "every side effect through the outbox"):** the poller answers `answerCallbackQuery` directly as a tap arrives, best effort. It only stops the button spinner - nothing in the chat changes - so exactly-once delivery isn't needed. Every other Telegram effect still goes through the dispatcher.
- **Also:** autocommit `read()` for hot read paths (1 round trip instead of 3), 30-minute pool recycle, immediate re-poll on timeouts, link previews disabled on every message and edit.
- **HR email:** "Send email to HR" queues an `email_draft` work item and answers at once. The address search runs up to 3 web searches and keeps only addresses on the company's own domain (its distinctive name in the host); a hiring inbox wins, else the general inbox (labelled in the preview), never a named person by default. If none, replying to the card with an address drafts to it. The writer (`email_writer_v2`) gets the claimable skills and the applicant's name, and one retry with what the checks refused; placeholders are refused.
- **Later (user agreed):** move the database to a Mumbai Supabase project for ~10x faster round trips.
- **Job Finder leads (same day):** the model ran one broad search, got only job-board listing pages, and found nothing. Code now runs the lead searches on employer job systems (`site:jobs.lever.co / jobs.ashbyhq.com / boards.greenhouse.io <title> India`, up to 6) and the model only picks single postings (`job_finder_v2`); a live check went from 0 to 6 leads.

## 19. Resume Reviewer and honest score raise (2026-10-06)

- **Context:** tailored resumes were mostly appended skill names: rewrites citing one item were refused for naming skills other confirmed items hold, and reorders failed on letter case. The user asked for a reviewer agent and a score above 95, choosing "honest max" over listing skills they lack.
- **Decision:** a fifth agent, the Resume Reviewer (`resume/review.py`, `prompts/resume/review_v1.md`), scores each tailored resume; below `resume.review_pass_score` (85) the tailor (`tailor_ops_v4`) revises once with its issues and the refused operations, and the better-reviewed attempt is kept. The verdict is stored in `parse_report.review`, shown on the PDF caption and the job page.
- **Checks stay strict but fair:** a rewrite naming a skill or title another confirmed item backs cites that item automatically (`claims.backing_items`); numbers must still be in the items the model cited; reorders match case-insensitively and never add or drop. The tailor and reviewer are told the exact claimable skill list.
- **Scoring rubric v2:** a JD skill your confirmed project / role / achievement / activity text names counts as matched (`how: experience`). Skills you don't have stay in the gap - 95+ only where the profile truly fits.

## 18. Salary is not a requirement for now (2026-10-06)

- **Context:** with a 25 LPA floor and a 0-2.5 year band, nearly every job was filtered or parked on salary, and salary estimates used up the 6 daily jobs-API calls that search needs.
- **Decision (user):** `salary.required: false` in `config/defaults.yaml`. The floor is ignored in hard filters and the pipeline; no jobs-API estimate or salary research runs; a stated salary is still read for the card.
- **Kept:** all salary code, the floor preference and decision 16's agent. Jobs held back by salary (parked, or filtered below the floor) are rechecked on the next run.
- **Revert:** set `salary.required: true`; tests pin both modes (`salary_required` fixture).

- **Superseded (2026-10-06):** salary checks were removed entirely, see decision 24.

## 17. The tailored resume may add related or quick-to-learn skills (2026-10-06)

- **Context:** the user wanted tailored resumes to raise the match score, not only reword.
- **Decision:** a missing job skill may be added to a skills line when the skills taxonomy knows it and it is related to a skill the resume already lists (prerequisite, implied, same category) or quick to learn (taxonomy depth <= 2). Soft skills and domains are never added. Code decides eligibility (`resume/skill_additions.py`); the model only places the skills.
- **Score:** the card shows "Match X% -> Y% with the tailored resume", where Y adds the gap points of each added skill. The confirmed-profile score itself does not change.
- **Limit:** skills unrelated to anything the user has stay on the learn list; claims in bullets still need confirmed evidence (decision 19).

## 16. ADR 16 - A fourth agent: Salary Research

**Status:** Accepted (2026-10-05, . Changes design principle 5 ("only three agents").

**Context.** Most Indian postings don't state pay. With a salary floor set, such jobs need an estimate; the jobs-API
estimate is limited by the free plan (6 calls/day), so jobs were parked as "salary unknown" and never carded.

**Decision.** `agents/salary_research/` searches the web (Tavily) for this company's pay for this role, on the shared
capped AgentRuntime (config `agents.salary_research`: about 8 jobs per run). It runs only when a floor is set, the job
states no salary and the jobs API has none. The model may only point at evidence; code checks the source was really
seen, finds the quoted text there word for word, and reads the figures from the quote. Confidence: the job's own
posting = stated, a page naming this company and role = medium, otherwise low (still parked, per design). The source
URL and quote are stored on the job (`salary_source`, `salary_text`). Parked jobs are re-checked each run; a job the
agent couldn't price waits 3 days before another try.

- **Superseded (2026-10-06):** salary checks were removed entirely, see decision 24.

## 15. ADR 15 - Layer clean-up, Option B tokens, deploy shape

**Status:** Accepted (2026-10-05).

1. Layers are enforced by `tests/unit/test_layers.py`. Shared: db, queue, security, llm, mcp_clients, integrations, guardrails, observability and the new `reference/` (normalize, skills catalog, salary text). Leaf features (discovery, filtering, scoring, learning, resume, outreach, profile, prep) import only shared layers. Orchestrators (run, telegram, cards, agents) combine features, as the guide's own examples do. `discovery` gets the Job Finder through a `LeadFinder` protocol; `prep` gets Prep Research as a function; resume import owns `resume/profile_drafts.py`.
2. Option B send tokens: Ed25519 (`APPROVAL_SIGNING_KEY` dispatcher, `APPROVAL_VERIFY_KEY` Gmail server; `make approval-keys`). With a verify key set, HMAC tokens are refused. Running the Gmail server as a separate OS user is still an ops step.
3. Card capacity reservations are a table (`card_reservations`, migration 9), not a JSON flag.
4. The laptop backend runs under launchd (`make autostart-install`), starting at login and restarting on exit.
5. Backups pick a pg_dump at least as new as the server, falling back to the `postgres:<major>` Docker image (Supabase runs Postgres 17).

## 14. ADR 14 - Fixes from the 2026-10-05 audit

**Status:** Accepted (2026-10-05).

1. Outbox partitions: only a card (`card:N`) and a run (`run:N`) are ordered lanes; every reply, alert and tap answer has its own partition. Tap answers older than 10 minutes, and edits of a vanished message, are *skipped*, never dead. Email sends are bounded by the 24-hour approval, not by attempts; caps and the kill switch *defer* (no attempt counted).
2. Exactly once: before any send the dispatcher checks Gmail Sent (deterministic Message-ID) **and** whether the draft still exists; a vanished draft with no Sent hit stops for manual reconcile. The first email retry waits 60 s for search indexing. The approved resume PDF is attached to the draft.
3. Never invent: every rewrite (and email) is checked for skills (taxonomy, whole words) and title words absent from its cited facts, besides numbers. Resume ops gained `rewrite_summary` and `reorder_skills` (prompts `tailor_ops_v2`, `job_chat_v2`). Bad operations are dropped, never raised; render failures fall back to the base resume.
4. Job Chat works during runs under the job's row lease; resolution is reply-to (any message of the card), a whole company/role name, the last 30 minutes' job, or a picker. Tier-2 can add a new profile item and snooze. Errors are answered, never retried silently. JD summary, JD-free "More..." menu, digest mode, `/pause`/`/resume` (a paused run keeps its id and checkpoint), `/digest`, reminders, outcomes, SerpApi fallback, jobs-API quota ledger, alerts, automatic kill switch, trace redaction, login lockout, SSRF IP pinning, robots.txt.
5. Deliberate deviations: the JD summary, the "explain changes" text and the HR-address extraction are deterministic code instead of the guide's `jd_summary`/`explain_changes`/`address_extract` prompts ("boring where possible"); `sources/normalize` and `matching/catalog` are treated as shared, pure vocabulary that other features may import.

## 13. 13: OpenAI GPT-4o mini

Date: 2026-10-04. Supersedes the provider/model choice in decision 3.

Use OpenAI `gpt-4o-mini` for all tasks, as requested. Credentials come from `OPENAI_API_KEY`
in the local/server environment. Never commit credentials. The Responses API sends strict JSON
schemas with `store=false`, then validates returned data using the existing Pydantic domain models.
Resume operation unions become `anyOf` on the wire; chat proposals enumerate supported keys,
with unused fields represented as null and removed before domain validation.

Refusals are recorded and raised without a retry. Invalid or incomplete output is recorded and
retried once. The existing ledger stores uncached input and cached input separately; OpenAI's
reported input count includes cached tokens, so cached input is subtracted before recording.
Prices include the alias and returned dated snapshot. Existing historical usage remains intact.

Shadow mode stays enabled. Offline tests cannot establish live model quality; run paid model
and staged workflow evaluations after supplying the key and before go-live.

Sources: [model documentation](https://developers.openai.com/api/docs/models/gpt-4o-mini),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## 12. 12 — Review fixes before provider testing

Use short transaction-scoped advisory locks and durable reservation flags for daily card capacity; pending Gmail outbox sends reserve email capacity and shadow sends count toward rollout caps.
Use unique resume object keys and allocate sequential versions inside the insert transaction, checking the original card/document version before committing chat edits.
Only pre-application cards can change their resume; dispatcher rechecks approval binding and reconciles Sent before checking expiry or sending. Expired/superseded sends are skipped; reconfirmation has a fresh approval key.
Tests ignore local credentials; CI runs Postgres-backed checks and offline gates. Citation validity is separate from held-out model quality, which remains unverified until the user supplies keys.
All sending stays in shadow mode. Live credentials, Gmail Sent contract, actual recall, cloud restore and deployment require the later staged verification described in `docs/operations.md`.

## 11. Phase 7 preparation corpus and grounding

## Status

Accepted

## Decision

Build preparation sets from the 32 PDFs marked `use: "prep"` in the checked-in book manifest. Extract text with Poppler's `pdftotext`, use PDF bookmarks where declared, and use a heading heuristic for unbookmarked files. A chunk is always a complete heading section and retains its full source text and page range; fixed-size token windows are not used. Only the model input used to calculate its vector is capped, so an unusually large section cannot monopolize local CPU time.

Store derived chunks in the `prep_chunks` Qdrant collection and rebuild that collection atomically. Retrieve by planned topic and skill overlap, then generate technical, behavioral, company, and DSA questions. A question is verified only when every citation names a retrieved chunk, a page fetched by the research agent, or a confirmed profile item. All other questions remain visible but are explicitly labelled unverified.

Known/Weak feedback is version checked and immediately rebuilds the study plan, moving weak topics to the front. The credential-free evaluation gate requires at least 90% grounded questions.

## Consequences

Corpus ingestion requires Poppler locally. This avoids a malformed-font path in `pypdf` that can stall text extraction while retaining `pypdf` for bookmark metadata. The manifest is authoritative: it currently has three unbookmarked prep PDFs even though an older design note says four. Live LLM and web-research quality checks remain deferred until credentials are supplied.

## 10. ADR 10 - P6 Job Chat safety boundary

**Status:** Accepted (2026-10-04).

Job Chat uses one typed, schema-validated model decision for one resolved job. Profile, current drafts and chat
memory are instructions; the job description is fenced untrusted data. Tier-1 actions may answer or create new
immutable resume/email versions, but cannot approve, apply or send. Tier-2 actions only create version-bound
pending proposals; a separate Confirm callback applies them and Cancel leaves state unchanged. Replies, `/chat`
queries and short selection tokens resolve jobs, while an active discovery run temporarily blocks Job Chat.

## 9. ADR 9 - P5 exactly-once outreach

**Status:** Accepted (2026-10-03).

Email drafts and sends are ordered Gmail outbox effects in the card partition. The durable send row contains an
approval id but no capability token; at delivery the dispatcher checks the exact job, email and version, enforces
the 24-hour window, and mints a two-minute HMAC token. The Gmail MCP refuses missing, invalid and mismatched
tokens. Before every send attempt the Gmail client searches Sent by the deterministic RFC 822 Message-ID, so a
crash after Gmail accepts but before the database commit reconciles instead of sending twice. `sent_emails.job_id`
is the final unique backstop. Shadow mode creates drafts and previews but logs sends without calling Gmail.

## 8. ADR 8 - Phase 4 job cards and resume versions

**Status:** Accepted (2026-10-03)

1. Card callbacks use random 64-bit URL-safe tokens stored on the card; payloads name the exact resume version and remain below Telegram's 64-byte limit.
2. Tailoring produces only typed rewrite/hide/move operations; code rejects unknown elements, unconfirmed citations, and every number absent from the cited profile facts.
3. Each tailored resume is immutable structured JSON plus a sandbox-rendered PDF, parse report, keyword coverage/repetition penalty, operations and a human-readable diff.
4. Telegram receives the PDF bytes through the send-only MCP from a validated private storage key; no public resume URL is required.
5. A later full JD re-enters extraction/scoring, creates a new resume version, resets stale approval, and edits the existing card through its ordered outbox partition.

## 7. ADR 7 - Phase 3 Job Finder

**Status:** Accepted (2026-10-03)

1. Brave Web Search is the search provider: one `SEARCH_API_KEY`, the official web-search endpoint, and no result storage beyond JobPilot's own sightings/cursors.
2. The web MCP is read-only and worker-only; fetch resolves before connecting, blocks private/special IPs, nonstandard ports and board deny-list domains, and revalidates every redirect.
3. The agent runtime enforces shared per-run search, page, step and token caps and emits one span per model/tool step.
4. Leads must use URLs returned by search; full JDs must come from fetched search-result pages and pass code-level company and title matching.
5. The agent only discovers leads and JDs. Existing deterministic code remains responsible for deduplication, filtering, salary decisions and scoring.

**Update 2026-10-05:** Tavily is the search provider in use (free developer plan; a `tvly-` key selects it); Brave stays supported. JSearch search moved to `/search-v2`.

## 6. ADR 6 - Phase 2: finding, de-duplicating, screening and scoring jobs

**Status:** Accepted (2026-10-03)

1. **Alert parsers key on job links, not layouts.** A job starts at a link whose URL (tracking redirects decoded)
   carries a board job id; the following text blocks give company, location, experience, salary. One parser module
   per board on a shared engine, an LLM fallback that may only return links present in the email, failures saved
   to `var/alert_failures/`, and the parse-yield alert. The test fixtures are **synthetic** emails modelled on the
   boards' formats - replace/extend them with real `.eml` files (`make alerts-check DIR=...`).
2. **Gmail MCP fixture mode.** `GMAIL_FIXTURE_DIR` serves `.eml` files (history id = position, `_history_min`
   simulates expiry), so the whole alert path runs before OAuth. Live mode: Gmail REST, refresh token encrypted
   with Fernet (`GMAIL_TOKEN_KEY`), scopes gmail.readonly + gmail.compose (phase 5 drafts) only.
3. **Embeddings** are local: fastembed `BAAI/bge-small-en-v1.5` (384 dims, ONNX, no key). Similarity is compressed
   (unrelated ~0.55, close ~0.8); calibration lives in `config/scoring.yaml`. Tests use a hashing embedder and keep
   fastembed/onnxruntime out of the test process (onnxruntime can abort in a C++ destructor at exit on macOS); the
   worker exits with `os._exit(0)` after cleanup for the same reason.
4. **Qdrant collections:** `profile_items` (point 0 = whole-profile vector with the profile hash; one point per
   confirmed project/role/achievement/summary) and `jobs` (point id = job id). Local embedded mode in `var/qdrant`.
5. **Dedup layer 3** (SimHash, 64-bit, word 3-shingles, <= 6 bits, same company only): reposts measure 3 bits, a
   rewritten role at the same company 7, unrelated JDs ~28.
6. **Preflight requires a confirmed profile** - scoring uses only confirmed facts.
7. **Without an API key** jobs stop at `pending_extraction` and rejoin the next run once the key exists.
8. **Salary estimates** (a jobs-API call) only when a salary floor is set; unknown salaries are parked, not dropped.
9. **Shared jobs** skip the snippet filters and stage A (the user chose them); hit = the job already had a sighting
   from another source.
10. **Extraction eval** starts with 10 synthetic labelled JDs; the design's gate is 50 real ones.

## 5. ADR 5 - Deploy: Vercel dashboard, laptop backend, Supabase, Qdrant

**Status:** Accepted (2026-10-03, . Supersedes the VM / private-network plan (decision 2, design 6.1).

**Decision.**
- Dashboard (http process) on **Vercel**. Public URL, so login is mandatory: the app returns 503 without
  `DASHBOARD_TOTP_SECRET`, requires a fixed `DASHBOARD_SESSION_SECRET`, sets secure cookies. It connects through
  Supabase's transaction pooler (NullPool, prepared statements off) and reads PDFs from Supabase Storage.
- **Worker + dispatcher on the user's laptop** (`make backend`). Vercel functions can't hold Telegram long polling,
  LISTEN/NOTIFY, MCP subprocesses, Tectonic or multi-minute runs. JobPilot is asleep until `/start`, so a
  laptop backend fits; work waits while it is off (Telegram keeps updates 24 h; events, work items and outbox rows
  wait in Supabase). Session connection (direct or session pooler) because of LISTEN/NOTIFY and advisory locks.
- **Supabase**: Postgres for everything relational; RLS enabled on every public table with no policies, so the
  auto-generated REST API exposes nothing (the app connects as table owner). Private Storage bucket for PDFs.
- **Qdrant** for all vectors (stage A matching, Prepare corpus) instead of pgvector. Local embedded mode in dev.

**Consequences.** Two data stores (Postgres + Qdrant): vectors are derived data and can be rebuilt from Postgres.
The Telegram MCP server, Tectonic sandbox (macOS sandbox-exec) and Gmail token live on the laptop only.
The Supabase service role key is needed by both the laptop and Vercel (Storage); treat it as a top secret.
Threat model change: the dashboard is now internet-facing; TOTP + CSRF + nonce + rate limiting at Vercel matter.

## 4. ADR 4 - Phase 1: profile items, structured resume, template, login

**Status:** Accepted (2026-10-03)

1. **Deterministic importer.** The user's .tex uses a small macro set (\resumesection, \datedrow, \skill, itemize).
   `resume/tex_import.py` parses it into the ResumeDoc with no LLM. render -> import round-trips exactly (tested,
   including hostile text). A resume in another format will need the LLM parser path later.
2. **The template** `templates/resume/resume.tex.j2` is the user's preamble copied verbatim plus template loops.
   Rendering the user's resume from data is pixel-identical (0 differing pixels at 100 dpi) to compiling their .tex.
3. **Profile item kinds** extend the design's list (skill/project/role/education/metric) with achievement (one
   factual statement, e.g. a bullet), activity and summary, so every resume line can cite an item whose text holds
   its claims. Skills are de-duplicated by the user's wording; the taxonomy id is an attribute (never guessed).
4. **Unicode.** Tectonic is XeTeX and the template uses T1 Type 1 fonts: raw Unicode punctuation silently vanished
   ("2020–2024" rendered as "20202024"). The escaper maps known characters to LaTeX commands and refuses others.
5. **Sandbox.** macOS: `sandbox-exec` profile (no network, writes only to the job dir and Tectonic cache) +
   `tectonic --untrusted --only-cached`. Linux: `--network none --read-only` container (deploy/sandbox, untested).
6. **Dashboard login.** TOTP (RFC 6238) once DASHBOARD_TOTP_SECRET is set; without it the dashboard refuses to bind
   beyond loopback and shows a banner. CSRF token on every POST; a per-form nonce makes double submits no-ops;
   every dashboard action is recorded in `events`, like a tap.
7. **/prefs** applies changes directly: it is a command the owner types, with no LLM in the loop. Job Chat (phase 6)
   can only propose preference changes.
8. **Notes parser** (LLM) drops any extracted item whose evidence quote is not literally in the notes.

## 3. ADR 3 - Phase 0 implementation choices

**Status:** Accepted (2026-10-03)

1. **Work queue table is `work_items`.** The design's `queue` table is the ranked *jobs* queue (phase 2); units of
   worker work (Telegram events, run requests) live in `work_items`, claimed with `FOR UPDATE SKIP LOCKED`.
2. **Telegram dry-run.** With `TELEGRAM_BOT_TOKEN` empty the Telegram MCP server writes each message to
   `var/telegram_dry_run.jsonl` and returns fake message ids, so the whole pipeline runs before setup.
   `scripts/fake_update.py` stores a simulated owner message exactly as the poller would.
3. **Telegram delivery is at-least-once.** If the dispatcher dies after Telegram accepted a message but before the
   database write, the message is sent again on restart. Emails get the Sent-folder check (decision 1); Telegram
   messages do not need it. Restores reconcile pending card messages separately (design 6.6).
4. **Outbox partitions.** Card messages use `card:<id>`; replies, run summaries and alerts use `chat`.
   Sequence numbers come from an upserted `outbox_partitions` row, so seq order equals commit order.
5. **MCP caller keys.** Each connection passes `MCP_CALLER` and `MCP_CALLER_KEY` to its server subprocess; empty
   keys fall back to fixed dev keys so the skeleton runs before `.env` exists. The MCP SDK starts subprocesses with a
   minimal environment, so the settings each server needs are passed through explicitly.
6. **Models.** `claude-opus-5-5` for tailoring, email, agents; `claude-haiku-4-5` for extraction and parsing.
   Opus calls send the server-side refusal fallback (`fallbacks: "default"`). Structured output via
   `messages.parse(output_format=<Pydantic model>)`; system prompt cached.
7. **LangGraph checkpoint tables** are created by `AsyncPostgresSaver.setup()` (`make migrate`), outside Alembic.
8. **One dispatcher** is enforced with a Postgres advisory lock; `DISPATCHER_START_PAUSED=1` starts it paused.

## 2. ADR 2 - Reaching the dashboard over a private network (P0 spike)

**Status:** Superseded by decision 5 (dashboard on Vercel behind TOTP). Still true for local development.

**Decision.** Nothing is exposed to the internet. Locally the http process binds to 127.0.0.1:8765 (port 8000 was
already taken on the dev laptop). On the VM, docker-compose publishes only `127.0.0.1:8765`, and the dashboard is
reached through Tailscale (`tailscale serve` provides TLS on the tailnet). Telegram uses long polling, so no
inbound port is needed at all. Single-user login (passkey or TOTP) arrives with the first page holding personal data.

## 1. ADR 1 - Finding an API-sent email in Gmail (P0 spike) - decided on real Gmail

**Status:** Accepted, verified 2026-10-05 with `make gmail-spike` against the user's own mailbox (test emails to self).

**Context.** Exactly-once email (design 6.5) needs a reliable "was this already sent?" check before any retry.

**What real Gmail does** (drafts.create + drafts.send through the API):
1. The sent message stays in the draft's **thread** and is labelled SENT.
2. The custom **`X-JobPilot-Job: <job_id>`** header is kept on the sent message.
3. A custom **Message-ID is replaced** by Gmail (`<CA...@mail.gmail.com>`), so an `rfc822msgid:` search never finds it.
4. The **draft is deleted** after the send.

**Decision.** Each draft carries `X-JobPilot-Job: <job_id>`, and the draft's thread id is stored (`emails.gmail_thread_id`,
migration 10) and sent along in the send row. Before every send attempt the dispatcher asks the Gmail server for
a SENT message in that thread carrying this job's header. If found, it is recorded as sent and never resent. If the
draft is gone but no sent message is found, the row stops for manual reconciliation (`make reconcile`), never a blind
resend. `sent_emails.job_id` UNIQUE stays the final backstop. The earlier `rfc822msgid:` plan is dropped.
