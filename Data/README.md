# JobPilot data

Clean, ready-to-load data for JobPilot. This folder contains no code. Chunking, embedding and ingestion happen in the project code.
Built on 2026-10-03 from `Raw_data/`, where the originals are still kept.

```
Data/
├── taxonomy/skills.json                  295 canonical skills (ids, aliases, prerequisites)
├── learning/resources.json               755 verified free learning resources (404 YouTube, 351 web)
├── learning/skill_resources.json         skill_id -> ranked resource ids (the gap -> resource lookup)
├── learning/projects_and_checkpoints.json 15 portfolio projects + 12 interview checkpoints, skill-tagged
├── books/books.json                      manifest for the 33 books (32 to ingest, 1 excluded)
├── books/pdfs/<book_id>.pdf              the 32 book PDFs, renamed to their ids
└── reports/                              coverage.json, dropped_resources.json (64 removed, each with a reason)
```

## How the project uses it

| Feature | File | How |
|---|---|---|
| Requirement extraction / scoring | `taxonomy/skills.json` | Normalize every extracted JD skill and profile skill to a skill `id` by matching `id`, `name` or any `aliases` (lower-cased). This replaces the "skill-synonym map" in `config/scoring.yaml`. |
| Gap -> learning resources | `learning/skill_resources.json` + `resources.json` | For each gap `skill_id`, take `resource_ids` in order (already ranked) and show the first 1-2. If a skill has none, walk its `prerequisites`. Only show URLs from this catalog: the LLM never writes links. |
| Learning order | `taxonomy/skills.json` | `prerequisites` and `depth` (0 = no prerequisites) order a multi-skill plan. |
| Prepare page (RAG corpus) | `books/books.json` | Ingest books with `use: "prep"`. `skill_ids` tags each book; `has_bookmarks` says whether PDF bookmarks can drive heading-based splitting (4 books have none). |
| Study-plan ideas | `learning/projects_and_checkpoints.json` | Suggest a project or mock-interview prompt that covers the user's gap skills. |

Skills with `resources_expected: false` are soft skills or business domains (communication, fintech, ...). They count in scoring but get no course links.

## Schemas

**skills.json → `skills[]`**
`id` (kebab-case, primary key) · `name` · `category` (language, backend, ml, cloud, soft, domain, ...) · `aliases[]` · `prerequisites[]` (skill ids) · `implies[]` · `depth` · `resources_expected`
Aliases are unique across all skills; prerequisites have no cycles.

**resources.json → `resources[]`**
| field | notes |
|---|---|
| `id` | stable primary key (`yt-...`, `web-...`, `free-...`) |
| `title`, `provider`, `url` | the real title and channel/publisher (YouTube ones verified via oEmbed) |
| `media` | `youtube` or `web` |
| `kind` | `video`, `playlist`, `course`, `docs`, `tutorial`, `guide`, `book`, `practice`, `article`, `paper` |
| `primary_skill_id`, `skill_ids[]` | every id exists in skills.json |
| `level` | `beginner`, `intermediate`, `advanced` |
| `est_hours` | an estimate, for planning only |
| `description`, `description_is_generated` | `true` = short placeholder built from title/skill (mostly YouTube videos, because YouTube rate-limited the fetch); replace it when you have the real text |
| `free`, `account_required`, `notes` | `notes` flags "free to audit" courses and similar |
| `video_count` | playlists only, when known |
| `link_status`, `link_note`, `checked_at` | HTTP result on 2026-10-03. 401/403 = site blocks bots, but the page exists |
| `source` | which input it came from: `youtube_courses.json`, `free_learning_platforms.json`, `curated_seed`, `gap_fill` |

**books.json → `books[]`**
`id` · `title` · `authors` · `year` · `license` · `file` (relative to `books/`, `null` if excluded) · `original_file` · `pages` · `has_bookmarks` · `bookmark_count` · `language` · `skill_ids[]` · `use` (`prep` | `exclude`) · `notes`

## Known gaps

- 92 technical skills have docs or courses but **no video** (list in `reports/coverage.json`, `skills_without_video`).
- The books don't cover system design, behavioral interviews, Java/Spring, Go or Docker/Kubernetes.
- `rl-notes-russian` (raw file 33) is in Russian and is excluded.
- Links rot: re-check `url`s periodically and hide anything that stops returning 200/401/403.
