# Deployment guide — Hostinger VPS

Target: `https://clipforge.nayankunwar.com` on Ubuntu 26.04, Docker Compose,
8 GB RAM / ~96 GB disk.

## 1. DNS

In the Hostinger DNS panel:

| Type | Name | Value |
| --- | --- | --- |
| A | `clipforge` | VPS IPv4 |
| AAAA | `clipforge` | VPS IPv6 (if enabled) |
| CAA | `@` | `0 issue "letsencrypt.org"` (only if you use CAA) |

Brevo will add SPF/DKIM/DMARC records after domain verification. Do not add a
second SPF record for the same domain.

## 2. VPS hardening

```bash
adduser deploy && usermod -aG sudo deploy
# copy your SSH key to deploy, then disable root/password SSH in /etc/ssh/sshd_config
apt update && apt upgrade -y
apt install -y ufw fail2ban unattended-upgrades
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Install Docker Engine + Compose from Docker's apt repository (not snap):

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

## 3. Install ClipForge

```bash
sudo mkdir -p /opt/clipforge && sudo chown deploy:deploy /opt/clipforge
git clone https://github.com/nayan-kunwar/clipforge.git /opt/clipforge
cd /opt/clipforge
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

- `DOMAIN=clipforge.nayankunwar.com`
- `CLIPFORGE_PUBLIC_BASE_URL=https://clipforge.nayankunwar.com`
- `CLIPFORGE_SESSION_COOKIE_SECURE=true`
- `CLIPFORGE_ALLOWED_HOSTS=clipforge.nayankunwar.com`
- `CLIPFORGE_ADMIN_EMAIL` / `CLIPFORGE_ADMIN_INITIAL_PASSWORD`
- `CLIPFORGE_SECRET_KEY` and `CLIPFORGE_TOKEN_ENCRYPTION_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `TOKEN_ENCRYPTION_KEY` backed up: losing it requires users to reconnect
Google.

## 4. Start

```bash
docker compose up -d --build
docker compose logs -f web
curl -fsS https://clipforge.nayankunwar.com/healthz
```

Caddy obtains the TLS certificate automatically. Open the URL, sign in with the
bootstrap admin, and change the password immediately.

## 5. Brevo

1. Verify `nayankunwar.com` in Brevo.
2. Add the provided SPF, DKIM, and DMARC records in Hostinger DNS.
3. Create an API key and set `CLIPFORGE_BREVO_API_KEY` in `.env`.
4. Set `CLIPFORGE_BREVO_SENDER_EMAIL=no-reply@nayankunwar.com`.
5. Restart: `docker compose up -d`.

Without a Brevo key, invite/reset links are returned in the admin UI for
copy/paste.

## 6. Google OAuth

1. Create a Google Cloud project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen (External).
3. Create an OAuth **Web application** client.
4. Redirect URI:

```text
https://clipforge.nayankunwar.com/api/v1/oauth/google/callback
```

5. Authorized domain: `nayankunwar.com`.
6. Scopes: `openid`, `email`, `https://www.googleapis.com/auth/youtube.upload`.
7. Set `CLIPFORGE_GOOGLE_CLIENT_ID` and `CLIPFORGE_GOOGLE_CLIENT_SECRET`.
8. Restart the stack.

Testing-mode OAuth refresh tokens expire after 7 days. For persistent per-user
uploads, publish/verify the OAuth app. Until verification, Google may force
uploads to Private. The UI always offers the manual download + Studio fallback.

## 7. YouTube quota

Default Google Cloud quota is 10,000 units/day; `videos.insert` costs ~1,600.
All users share that quota, so the default caps are 5 automated uploads/day
globally and 2/user/day. Adjust in the admin settings if needed, or request a
quota extension. Manual uploads do not consume API quota.

## 8. Backups

```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```

Schedule it with cron:

```cron
17 3 * * * cd /opt/clipforge && BACKUP_DIR=/var/backups/clipforge ./scripts/backup.sh >> /var/log/clipforge-backup.log 2>&1
```

Copy backups offsite (restic, rclone, Hostinger Storage Box) and test a restore
with `scripts/restore.md`. Also enable weekly Hostinger VPS snapshots for full
media recovery.

## 9. Updates

```bash
cd /opt/clipforge
git pull
docker compose up -d --build
docker image prune -f
```

Rebuild monthly to keep yt-dlp and base packages current. YouTube extractor
changes are frequent; a stale yt-dlp will eventually fail downloads.

## 10. Capacity notes

- 96 GB disk → roughly 4–5 users at the full 15 GB quota.
- The 8 GB RAM tier is often 2 vCPU; transcription is disabled by default.
- One media worker renders one clip at a time. Queue times grow with users.
- If YouTube blocks downloads from the VPS IP, the local-upload path still
  works. An admin-provided cookies file can be added via `YTDLP_COOKIES_FILE`
  (not enabled by default).

## 11. Troubleshooting

| Symptom | Check |
| --- | --- |
| TLS fails | DNS A/AAAA, ports 80/443, Caddy logs |
| 400 Invalid host header | `CLIPFORGE_ALLOWED_HOSTS` must include the domain |
| Login loop | `CLIPFORGE_SESSION_COOKIE_SECURE` must match HTTPS/HTTP |
| Invite email missing | Brevo key, sender verification, SPF/DKIM, `email_log` |
| Upload rejected | 4 GB / 2 hour / 15 GB limits and disk free space |
| Render stuck | `docker compose logs -f worker-media`; admin job queue |
| Google upload fails | Reconnect Google; check quota caps and OAuth testing expiry |
