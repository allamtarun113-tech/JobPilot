# Move the database to Mumbai

**Why:** the database (Supabase) and the dashboard (Vercel) are in Sydney, Australia. Every step the bot or the
dashboard takes crosses the ocean and back, about 0.3 s per round trip from India and up to 6 s to open a new
connection. In Mumbai (AWS `ap-south-1`), each step is roughly 5-10x faster.

**Cost:** free. Supabase's free plan allows two projects, and Vercel lets you choose a region on the free plan.

**Time:** about 10 minutes for you (Part A), then about 15 minutes of commands (Part B), which Claude Code can
run for you.

Nothing is deleted. The old Sydney project stays untouched until you remove it yourself (Part C).

---

## Part A: create the Mumbai project (you, in the browser)

1. Open https://supabase.com/dashboard and sign in.
2. Click **New project**.
   - **Organization:** the same one as your current JobPilot project.
   - **Project name:** `jobpilot-mumbai`
   - **Database password:** click **Generate a password**, then **copy it somewhere safe**. You need it in
     step 5.
   - **Region:** **South Asia (Mumbai)**
   - **Plan:** Free.
   - Click **Create new project** and wait about 2 minutes until it says the project is ready.
3. **Project URL.** Left menu → **Project Settings** (gear icon) → **Data API**. Copy **Project URL**
   (looks like `https://abcd1234.supabase.co`).
4. **Service key.** Project Settings → **API Keys** → tab **Legacy API Keys** → next to `service_role` click
   **Reveal**, then copy it (a long text starting with `eyJ`). Treat it like a password.
5. **Connection strings.** Click the **Connect** button at the top of the project page → tab
   **Connection String** → type **URI**.
   - Copy the **Session pooler** string (port `5432`).
   - Copy the **Transaction pooler** string (port `6543`).
   - In both, replace `[YOUR-PASSWORD]` with the password from step 2.
   - In both, change the start from `postgresql://` to `postgresql+psycopg://`.
6. Open the `.env` file in the JobPilot folder and add these four lines at the bottom, with your values. Don't
   change the existing lines yet; the move reads both old and new.

   ```
   NEW_DATABASE_URL=postgresql+psycopg://...:5432/postgres        (session pooler, step 5)
   NEW_DATABASE_POOLER_URL=postgresql+psycopg://...:6543/postgres (transaction pooler, step 5)
   NEW_SUPABASE_URL=https://....supabase.co                       (step 3)
   NEW_SUPABASE_SERVICE_ROLE_KEY=eyJ...                           (step 4)
   ```

7. Save `.env`, then tell Claude Code: **"Mumbai project is ready"**. Never paste these values into a chat.

---

## Part B: move the data (commands; Claude Code can run these)

Run from the JobPilot folder. `OLD_*` means today's Sydney values from `.env`, and `NEW_*` the lines you just
added.

1. **Stop the laptop backend** so nothing changes during the move:
   `launchctl bootout gui/$(id -u)/com.jobpilot.backend`
2. **Fresh backup** of the Sydney database:
   `uv run --env-file .env python scripts/backup.py`
3. **Decrypt it** to a temporary file:
   `uv run --env-file .env python scripts/backup.py --decrypt Data/user/backups/<newest>.dump.enc /tmp/jobpilot.dump`
4. **Restore only JobPilot's tables** (the `public` schema) into Mumbai. Supabase runs Postgres 17, so use the
   matching tool through Docker:

   ```
   docker run --rm -v /tmp:/b postgres:17 pg_restore --no-owner --no-privileges --schema=public \
     -d "<NEW_DATABASE_URL with postgresql:// at the start>" /b/jobpilot.dump
   ```

5. **Finish the database setup** (migrations, locking every table with RLS, LangGraph tables):
   `DATABASE_URL=<NEW_DATABASE_URL> make migrate CONFIRM=yes`
6. **Check every table copied:**
   `OLD_DATABASE_URL=<old DATABASE_URL> DATABASE_URL=<NEW_DATABASE_URL> uv run python scripts/compare_databases.py`
   It must end with "All tables match."
7. **Create the private storage bucket and copy the resume PDFs:**

   ```
   SUPABASE_URL=<NEW_SUPABASE_URL> SUPABASE_SERVICE_ROLE_KEY=<NEW key> make storage-setup
   OLD_SUPABASE_URL=<old SUPABASE_URL> OLD_SUPABASE_SERVICE_ROLE_KEY=<old key> \
     DATABASE_URL=<NEW_DATABASE_URL> SUPABASE_URL=<NEW_SUPABASE_URL> SUPABASE_SERVICE_ROLE_KEY=<NEW key> \
     uv run python scripts/copy_storage.py
   ```

8. **Switch `.env` over:** replace the values of `DATABASE_URL`, `DATABASE_POOLER_URL`, `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY` with the `NEW_*` values, then delete the four `NEW_*` lines. Keep the old
   values in a safe note until Part C.
9. **Switch Vercel over** (the dashboard):
   - https://vercel.com → project **jobpilot** → **Settings** → **Environment Variables**.
   - Edit `DATABASE_URL`, `DATABASE_POOLER_URL`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (Production) to
     the new values.
   - In `vercel.json`, change `"regions": ["syd1"]` to `"regions": ["bom1"]` (Mumbai), then deploy:
     `npx vercel deploy --prod --yes`.
10. **Start the laptop backend again:** `make autostart-install`
11. **Check:**
    - `/status` in Telegram answers quickly;
    - the dashboard opens and shows your jobs and profile;
    - a resume PDF opens from a card;
    - `/start` completes a run.
12. **Delete the temporary dump:** `rm /tmp/jobpilot.dump`

---

## Part C: remove the Sydney project (you, after a week)

When everything has worked for a week:

1. Supabase dashboard → the old project → **Project Settings** → **General** → **Pause project**. This is
   reversible and keeps the data.
2. A few days later, if nothing is missing: **Delete project**.
3. Delete the old values from your safe note.

## If something goes wrong

Until step 8, nothing has changed for the app: the bot and dashboard still use Sydney. If step 4, 5 or 6
fails, fix it and run it again; the Mumbai project can be wiped and recreated freely. After step 8, switching
back means putting the old values back in `.env` and on Vercel.
