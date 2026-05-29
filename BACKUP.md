# Backup Procedures — Fazle Core

**Last updated:** 2026-05-07
**Applies to:** `/home/azim/core` (production Fazle Core instance)

---

## Overview

Fazle Core uses three backup layers:

| Layer | Method | Schedule | Location |
|---|---|---|---|
| PostgreSQL database | `pg_dump` (custom format) | Daily 02:30 Asia/Dhaka | `/home/azim/backups/fazle/` |
| Redis state | `BGSAVE` RDB snapshot | Manual / pre-chaos | `/home/azim/backups/redis/` |
| Project files | `tar.gz` snapshot | Manual / pre-chaos | `/home/azim/backups/` |
| Environment files | `cp` to secure store | Manual / on change | `/home/azim/secure-env-backup/` |

---

## 1. PostgreSQL Backup

### Automated Backup (APScheduler)

The backup module (`modules/backup`) runs daily at 02:30 Asia/Dhaka.
Files are named `fazle_pg_YYYYMMDD_HHMMSS.dump` in custom (-Fc) format.

Retention policy (configurable via env vars):
- `BACKUP_KEEP_DAILY=14` — keep 14 most recent daily backups
- `BACKUP_KEEP_WEEKLY=8` — keep 8 most recent Sunday backups

```bash
# Check backup status via API
curl -H "X-Internal-Key: <key>" http://localhost:8200/backup/status

# List all backups
curl -H "X-Internal-Key: <key>" http://localhost:8200/backup/list | python3 -m json.tool

# Trigger manual backup now
curl -X POST -H "X-Internal-Key: <key>" http://localhost:8200/backup/run

# Trigger rotation (prune old files)
curl -X POST -H "X-Internal-Key: <key>" http://localhost:8200/backup/rotate
```

### Manual pg_dump (Direct)

Use this when the API is unavailable or for off-schedule snapshots:

```bash
# Custom format (compressed, recommended — supports parallel restore)
docker exec ai-postgres pg_dump \
  -U postgres \
  -Fc \
  --no-owner \
  --no-privileges \
  -f /tmp/fazle_manual_$(date +%Y%m%d_%H%M%S).dump \
  postgres

# Copy from container to host
docker cp ai-postgres:/tmp/fazle_manual_*.dump /home/azim/backups/fazle/

# Verify dump integrity
docker exec ai-postgres pg_restore --list /tmp/fazle_manual_*.dump | head -20
```

```bash
# Plain SQL format (human-readable, larger file)
docker exec ai-postgres pg_dump \
  -U postgres \
  --no-owner \
  --no-privileges \
  postgres > /home/azim/backups/fazle/fazle_plain_$(date +%Y%m%d_%H%M%S).sql

# Schema only (no data)
docker exec ai-postgres pg_dump \
  -U postgres \
  --schema-only \
  --no-owner \
  postgres > /home/azim/backups/fazle/fazle_schema_$(date +%Y%m%d).sql

# Data only (no schema)
docker exec ai-postgres pg_dump \
  -U postgres \
  --data-only \
  --no-owner \
  postgres > /home/azim/backups/fazle/fazle_data_$(date +%Y%m%d).sql
```

### Verify Backup File

```bash
# Check file size (should be several MB for production)
ls -lh /home/azim/backups/fazle/ | sort -k5 -h

# Verify dump is not corrupted
docker cp /home/azim/backups/fazle/fazle_pg_YYYYMMDD.dump ai-postgres:/tmp/check.dump
docker exec ai-postgres pg_restore --list /tmp/check.dump > /dev/null && echo "VALID" || echo "CORRUPT"

# Check row counts in backup (requires restore to temp DB)
# See RECOVERY.md for dry-run restore procedure
```

---

## 2. Redis Backup

Redis stores rate limiting and session data. It is not the source of truth —
the PostgreSQL database is. Redis loss causes transient UX degradation only.

```bash
# Create Redis snapshot directory
mkdir -p /home/azim/backups/redis

# Trigger background save
docker exec ai-redis redis-cli -a <pass> BGSAVE

# Wait for save to complete
docker exec ai-redis redis-cli -a <pass> LASTSAVE

# Copy RDB file to host
docker cp ai-redis:/data/dump.rdb /home/azim/backups/redis/redis_$(date +%Y%m%d_%H%M%S).rdb

# Verify file
ls -lh /home/azim/backups/redis/
```

---

## 3. Full Project Snapshot (tar)

Captures all application code, configs (excluding secrets), and test assets.

```bash
# Create snapshot
tar -czf /home/azim/backups/core_snapshot_$(date +%Y%m%d_%H%M%S).tar.gz \
  --exclude='/home/azim/core/.venv' \
  --exclude='/home/azim/core/.git' \
  --exclude='/home/azim/core/__pycache__' \
  --exclude='/home/azim/core/logs/*.log' \
  --exclude='/home/azim/core/tests/coverage_html' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  /home/azim/core/

# Verify
tar -tzf /home/azim/backups/core_snapshot_*.tar.gz | head -20
ls -lh /home/azim/backups/core_snapshot_*.tar.gz
```

---

## 4. Environment File Backup

`.env` contains database passwords and API keys — never commit to git.

```bash
# Backup .env to secure store (restricted permissions)
cp /home/azim/core/.env /home/azim/secure-env-backup/core.env.$(date +%Y%m%d)
chmod 600 /home/azim/secure-env-backup/core.env.*

# Verify backup exists
ls -la /home/azim/secure-env-backup/

# Keep only last 5 .env backups
ls -t /home/azim/secure-env-backup/core.env.* | tail -n +6 | xargs -r rm
```

---

## 5. Pre-Chaos Backup Checklist

Run these commands before starting any chaos testing:

```bash
#!/bin/bash
# pre_chaos_backup.sh

set -e
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_BASE="/home/azim/backups"

echo "[1/4] PostgreSQL dump..."
docker exec ai-postgres pg_dump -U postgres -Fc --no-owner --no-privileges \
  -f /tmp/pre_chaos_${TIMESTAMP}.dump postgres
docker cp ai-postgres:/tmp/pre_chaos_${TIMESTAMP}.dump \
  ${BACKUP_BASE}/fazle/pre_chaos_${TIMESTAMP}.dump
echo "  -> ${BACKUP_BASE}/fazle/pre_chaos_${TIMESTAMP}.dump"

echo "[2/4] Redis snapshot..."
docker exec ai-redis redis-cli -a "$REDIS_PASS" BGSAVE
sleep 2
docker cp ai-redis:/data/dump.rdb \
  ${BACKUP_BASE}/redis/pre_chaos_${TIMESTAMP}.rdb
echo "  -> ${BACKUP_BASE}/redis/pre_chaos_${TIMESTAMP}.rdb"

echo "[3/4] Project snapshot..."
tar -czf ${BACKUP_BASE}/core_pre_chaos_${TIMESTAMP}.tar.gz \
  --exclude='/home/azim/core/.venv' \
  --exclude='/home/azim/core/.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  /home/azim/core/
echo "  -> ${BACKUP_BASE}/core_pre_chaos_${TIMESTAMP}.tar.gz"

echo "[4/4] Environment backup..."
cp /home/azim/core/.env \
  /home/azim/secure-env-backup/core.env.pre_chaos_${TIMESTAMP}
echo "  -> /home/azim/secure-env-backup/core.env.pre_chaos_${TIMESTAMP}"

echo ""
echo "Pre-chaos backup complete: ${TIMESTAMP}"
echo "Files:"
ls -lh ${BACKUP_BASE}/fazle/pre_chaos_${TIMESTAMP}.dump
ls -lh ${BACKUP_BASE}/redis/pre_chaos_${TIMESTAMP}.rdb
ls -lh ${BACKUP_BASE}/core_pre_chaos_${TIMESTAMP}.tar.gz
```

---

## 6. Backup Verification

After every backup, verify:

```bash
# 1. File exists and is non-zero
ls -lh /home/azim/backups/fazle/fazle_pg_*.dump | tail -3

# 2. pg_restore can read the file
LATEST=$(ls -t /home/azim/backups/fazle/*.dump | head -1)
docker cp "$LATEST" ai-postgres:/tmp/verify.dump
docker exec ai-postgres pg_restore --list /tmp/verify.dump > /dev/null \
  && echo "BACKUP VALID" || echo "BACKUP CORRUPT"

# 3. SHA256 checksum (store alongside backup for integrity checks)
sha256sum /home/azim/backups/fazle/fazle_pg_*.dump | tail -3
```

---

## 7. Backup Locations Summary

```
/home/azim/backups/
  fazle/                    # PostgreSQL dumps
    fazle_pg_YYYYMMDD_HHMMSS.dump   # automated daily
    pre_chaos_TIMESTAMP.dump         # pre-chaos snapshots
  redis/                    # Redis RDB snapshots
    redis_YYYYMMDD_HHMMSS.rdb
    pre_chaos_TIMESTAMP.rdb
  core_snapshot_*.tar.gz    # project file snapshots
  core_pre_chaos_*.tar.gz   # pre-chaos project snapshots

/home/azim/secure-env-backup/
  core.env.YYYYMMDD         # .env file backups
  runtime-services.env      # auto-generated runtime URLs
```
