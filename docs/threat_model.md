# Threat model (design 7.2, v1 = Option A)

The MCP servers are subprocesses of the app under the same OS user, so their caller keys, scope checks and the send-token
check protect against **bugs** (a feature calling send by mistake, a missing approval), not a compromised app process.
Accepted for a single-user tool. Upgrade path (Option B): the Gmail MCP server as a separate OS user/container holding the
Gmail token and a verification key, with asymmetric send tokens so the app cannot mint them.

| Asset / adversary | Controls in this codebase | Residual risk |
|---|---|---|
| Gmail send vs an app bug | Read-only worker vs send-capable dispatcher connections (`config/mcp_scopes.yaml`, checked in app and server); durable approval + 2-minute HMAC token minted only at delivery; Sent reconciliation + draft-existence check before any retry; `sent_emails.job_id` UNIQUE; caps; shadow mode | - |
| Gmail send vs a compromised app | Laptop-only secrets; dependency pinning (`uv.lock`) | Could send email as you - accepted for v1 (Option B removes it) |
| Dashboard on a public URL (decision 5) | TOTP login, lockout after 5 wrong codes in 15 min, no code reuse, 12-hour sessions, CSRF + nonce on every POST, `no-store`, minimal public `/health`, HTMX pinned with SRI | Phishing of your TOTP code |
| Profile / resume / chat vs a stolen laptop | Gmail token Fernet-encrypted; backups Fernet-encrypted; Data/user and .env git-ignored | Disk encryption (FileVault) is your responsibility |
| Your accounts vs a malicious JD or web page | Sanitising (instruction-like text stripped, flagged), fenced data blocks (JD, drafts, chat history), tier-2 Job Chat changes only as proposals, claim checks on every rewrite (skills, titles, numbers), no raw LaTeX, sandboxed Tectonic (no network, no reads of your home folder) | Low |
| Internal network vs the web fetcher (SSRF) | Resolve first, public IPs only, connect to the checked IP (no DNS rebinding), every redirect re-checked, standard ports, size/time caps, board deny-list, robots.txt | Low |
| Approvals vs a Telegram account takeover | Chat-id lock in the poller and the Telegram server; caps; one email per job and per company per 30 days; alerts | An attacker could approve a few sends - bounded by caps |
| Recruiters' contact data | Masked in traces and in event payloads after 180 days; processors listed in docs/processors.md | Low |
| API keys | `.env` only, never in prompts or traces; rotate after any exposure | Low |
