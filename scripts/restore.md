# Restore runbook

Backups created by `scripts/backup.sh` contain:

- `clipforge-YYYYMMDD-HHMMSS.db` — SQLite database (users, projects, jobs, OAuth tokens)
- `manifests-YYYYMMDD-HHMMSS.tar.gz` — per-project `CLIPS_MANIFEST.json` files
- `env-YYYYMMDD-HHMMSS.txt` — environment file, including secrets

Media files live on the `clipforge_data` Docker volume. The logical backup does
not include sources or rendered MP4s; take a Hostinger VPS snapshot for full
media recovery.

## Restore the database and manifests

Stop the app:

```bash
cd /opt/clipforge
docker compose stop web worker-media worker-upload
```

Restore the database through a one-off container (keeps volume permissions):

```bash
docker compose run --rm --no-deps -T web sh -c 'cat > /data/clipforge.db' \
  < backups/clipforge-YYYYMMDD-HHMMSS.db
```

Restore manifests:

```bash
docker compose run --rm --no-deps -T web \
  sh -c 'tar -xzf - -C /data/users' \
  < backups/manifests-YYYYMMDD-HHMMSS.tar.gz
```

Start the services:

```bash
docker compose up -d
docker compose logs -f web
```

Verify `/healthz`, sign in, and open a project. If Google tokens were encrypted
with a different `CLIPFORGE_TOKEN_ENCRYPTION_KEY`, users must reconnect Google.

## Restore full media

Use a Hostinger snapshot or copy the Docker volume:

```bash
docker run --rm -v clipforge_clipforge_data:/data -v "$PWD/backups:/backups" \
  alpine tar -czf /backups/media.tar.gz -C /data .
```

Restore with the reverse command, then restore the database and manifests.

## Test restores

Run a restore test quarterly into a throwaway stack or directory. A backup that
has never been restored is not a backup.
