# Run the backend in the cloud (when you're ready)

Today the **worker** (reads Telegram, finds and scores jobs, writes resumes) and the **dispatcher** (sends
messages and emails) run on your laptop. That works, but:

- when the laptop sleeps, nothing runs;
- messages you send while the laptop is off wait in Telegram for up to 24 hours, then expire.

Moving both to an always-on cloud server fixes this. The code is already prepared, so it is a setup task, not a
coding task.

## What's already ready

| File | What it is |
| --- | --- |
| `deploy/Dockerfile` | The backend as one container image: Python, all backend packages, Tectonic (for resume PDFs) and the embedding model, all pre-downloaded |
| `deploy/entrypoint.sh` | Starts the worker and dispatcher, and restores the Gmail token from a secret |
| `deploy/supervisor-cloud.conf` | Keeps both processes running; logs go to the host's log viewer |
| `deploy/fly.toml` | Ready-made settings for Fly.io in Mumbai |
| `.dockerignore` | Keeps `.env`, your private files and tests out of the image |

The image was built and tested on 2026-10-07 with networking switched off: it rendered a resume PDF and passed
the PDF check, and the embedding model and every process loaded.

Resume PDFs are built safely inside the container: no downloads (`--only-cached`) and no shell commands
(`--untrusted`). The code picks this mode by itself when it runs in the container (`JOBPILOT_IN_CONTAINER=1`).

## What the server needs

- About **2 GB of memory**, a normal Intel/AMD (`x86_64`) machine, running all the time.
- A region in India, near the database: **Mumbai** (`bom` on Fly.io, `ap-south-1` on AWS) or Bangalore
  (DigitalOcean).
- No open ports. The backend only makes outgoing connections.
- Cost: roughly $6-12 a month, depending on the host (check its pricing page).

## The settings (secrets) the server needs

Copy these from your laptop's `.env` into the host's secret settings. **Never put `.env` in the image.**

| Group | Names |
| --- | --- |
| Database and storage | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET` |
| Vectors | `QDRANT_URL`, `QDRANT_API_KEY` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| AI and jobs | `OPENAI_API_KEY`, `JSEARCH_API_KEY`, `JSEARCH_HOST` |
| Gmail | `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_TOKEN_KEY`, and `GMAIL_TOKEN_ENC_B64` (below) |
| Internal keys | `APPROVAL_TOKEN_KEY`, `MCP_TELEGRAM_DISPATCHER_KEY`, `MCP_TELEGRAM_WORKER_KEY`, `MCP_GMAIL_DISPATCHER_KEY`, `MCP_GMAIL_WORKER_KEY`, `MCP_WEB_WORKER_KEY` |
| Other | `JOBPILOT_ENV=prod`, `DASHBOARD_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` |

**Gmail token.** On the laptop it is the file `var/gmail_token.enc` (already encrypted). Turn it into one line
of text, and set that as the secret `GMAIL_TOKEN_ENC_B64`:

```
base64 -i var/gmail_token.enc | tr -d '\n'
```

`DATABASE_POOLER_URL` and the dashboard login secrets are only for Vercel; the backend doesn't need them.

## Important: only one backend at a time

Telegram allows only one program to read the bot's messages, and the dispatcher must run once. **Stop the
laptop backend before starting the cloud one:**

```
make autostart-uninstall
```

## Option 1: Fly.io (simplest)

1. Create an account at https://fly.io and add a card.
2. Install the command-line tool: `brew install flyctl`, then `fly auth login`.
3. From the JobPilot folder, create the app once (the name must be unique on Fly; change it in `deploy/fly.toml`
   if it's taken):
   `fly apps create jobpilot-backend`
4. Add the secrets. The easiest way is from a copy of `.env` holding only the names in the table above:

   ```
   fly secrets import -c deploy/fly.toml < backend.env
   fly secrets set -c deploy/fly.toml GMAIL_TOKEN_ENC_B64="$(base64 -i var/gmail_token.enc | tr -d '\n')"
   ```

   Delete `backend.env` afterwards.
5. Stop the laptop backend: `make autostart-uninstall`
6. Deploy (Fly builds the image from `deploy/Dockerfile`):
   `fly deploy -c deploy/fly.toml --dockerfile deploy/Dockerfile`
7. Watch the logs: `fly logs -c deploy/fly.toml`. You should see `worker ... started` and
   `dispatcher started (telegram live)`.
8. Send `/status` in Telegram to confirm it answers.

Updating after a code change: run the same `fly deploy` command again.

## Option 2: any small server (DigitalOcean, AWS Lightsail, ...)

1. Create an Ubuntu server with 2 GB of memory in Mumbai or Bangalore, and log in with SSH.
2. Install Docker: `curl -fsSL https://get.docker.com | sh`
3. Copy the project (without `.env`, `Data/user`, `var`) to the server, for example with `git clone` or `rsync`.
4. Create `/opt/jobpilot/backend.env` with the secrets from the table (one `NAME=value` per line), including
   `GMAIL_TOKEN_ENC_B64`. Then `chmod 600 /opt/jobpilot/backend.env`.
5. Build: `docker build -f deploy/Dockerfile -t jobpilot-backend .`
6. Stop the laptop backend: `make autostart-uninstall`
7. Run it, restarting automatically after crashes and reboots:

   ```
   docker run -d --name jobpilot --restart unless-stopped --env-file /opt/jobpilot/backend.env jobpilot-backend
   ```

8. Logs: `docker logs -f jobpilot`. Updating: `git pull`, build again, then
   `docker rm -f jobpilot` and run step 7 again.

## Going back to the laptop

Stop the cloud backend (`fly scale count 0 -c deploy/fly.toml`, or `docker rm -f jobpilot`), then on the
laptop: `make autostart-install`.

## Building the image on a Mac

Apple Silicon Macs build ARM images by default, and Tectonic has no ARM Linux build. Add
`--platform linux/amd64`:

```
docker build --platform linux/amd64 -f deploy/Dockerfile -t jobpilot-backend .
```
