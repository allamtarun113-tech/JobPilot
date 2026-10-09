# Data processors and destinations

This is an inventory of configured code paths, not a claim about provider contracts or retention policies.

- **OpenAI:** confirmed profile facts, job descriptions, resume content and task-specific prompts for extraction, tailoring, email writing, chat and preparation. API keys remain local/server environment values. Confirm account data-retention/training settings before supplying personal data.
- **Google Gmail:** creates approved-flow outreach drafts and checks Sent before a retry; sends only through the dispatcher with a valid approval token. Stored refresh tokens are encrypted locally with `GMAIL_TOKEN_KEY`. OAuth can be revoked in the Google account.
- **Telegram:** inbound commands and outgoing cards, resume PDFs, previews and approval buttons. Restrict the bot to the configured owner chat.
- **OpenAI web search** (the `web_search` tool, same `OPENAI_API_KEY`; decision 21): job-search and company/preparation queries; public pages are fetched directly through the bounded web MCP server. Search queries may reveal job preferences or target companies.
- **JSearch/RapidAPI and SerpApi (fallback):** role/location job searches.
- **Supabase:** optional database and private object storage containing profile, resume, job/chat, approval and delivery records. The service key must remain server-side; public schema RLS is enabled by migration setup.
- **Qdrant:** optional hosted vectors with corpus/profile/job payloads; local mode stores them on the laptop instead. Embeddings themselves are generated locally.
- **Langfuse/OTLP tracing:** observability metadata, counts and errors; email addresses and phone numbers are masked before export. Set 30-day trace retention in the Langfuse project.
- **Vercel:** intended dashboard host; handles requests/signed sessions and reads Supabase. Deployment is not enabled merely by this inventory.

Local runtime destinations include `Data/user/`, `var/`, the database, encrypted Gmail token storage, local vector storage and compiler/model caches. User files, `.env` and runtime logs are git-ignored. Reference books and learning data are distinct from private runtime files.

Before deployment, choose retention periods, confirm provider settings and regions, verify access controls, and test export/deletion and encrypted restore. Do not assume this inventory establishes legal compliance.
