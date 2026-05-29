# Pre-Chaos Testing Checklist

**Purpose:** Verify system is in a known-good, recoverable state before any chaos/soak testing.
**Required:** All items must be GREEN before starting chaos tests.
**Date:** _______________

---

## Section A — Backup Verification

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| A1 | PostgreSQL backup created (< 2h ago) | `ls -lt /home/azim/backups/fazle/*.dump \| head -3` | Non-zero file, recent timestamp | [ ] |
| A2 | Backup file is not corrupted | `docker exec ai-postgres pg_restore --list /tmp/verify.dump > /dev/null` | Exit code 0 | [ ] |
| A3 | Redis snapshot taken | `ls -lt /home/azim/backups/redis/*.rdb \| head -3` | Non-zero file, recent timestamp | [ ] |
| A4 | .env backed up | `ls -lt /home/azim/secure-env-backup/core.env.* \| head -3` | Recent file exists | [ ] |
| A5 | Project snapshot taken | `ls -lt /home/azim/backups/core_pre_chaos_*.tar.gz \| head -3` | Recent file exists | [ ] |

```bash
# Run backup commands
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# A1 + A2: PostgreSQL
docker exec ai-postgres pg_dump -U postgres -Fc --no-owner --no-privileges \
  -f /tmp/pre_chaos_${TIMESTAMP}.dump postgres
docker cp ai-postgres:/tmp/pre_chaos_${TIMESTAMP}.dump \
  /home/azim/backups/fazle/pre_chaos_${TIMESTAMP}.dump
docker exec ai-postgres pg_restore --list /tmp/pre_chaos_${TIMESTAMP}.dump > /dev/null \
  && echo "A1+A2: PASS" || echo "A1+A2: FAIL"

# A3: Redis
docker exec ai-redis redis-cli BGSAVE && sleep 2
docker cp ai-redis:/data/dump.rdb \
  /home/azim/backups/redis/pre_chaos_${TIMESTAMP}.rdb
echo "A3: PASS"

# A4: .env
cp /home/azim/core/.env /home/azim/secure-env-backup/core.env.pre_chaos_${TIMESTAMP}
echo "A4: PASS"

# A5: Project snapshot
tar -czf /home/azim/backups/core_pre_chaos_${TIMESTAMP}.tar.gz \
  --exclude='/home/azim/core/.venv' --exclude='__pycache__' --exclude='*.pyc' \
  /home/azim/core/
echo "A5: PASS"
```

---

## Section B — Git / Code State

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| B1 | Working tree is clean (no uncommitted changes) | `git status` | `nothing to commit` | [ ] |
| B2 | All tests committed | `git diff --cached --name-only` | Empty | [ ] |
| B3 | Checkpoint tag exists | `git tag -l pre-chaos-stable-2026-05-07` | Tag present | [ ] |
| B4 | On correct branch | `git branch --show-current` | `main` or `develop` | [ ] |

```bash
cd /home/azim/core
git status                                  # B1
git diff --cached --name-only               # B2
git tag -l | grep pre-chaos                 # B3
git branch --show-current                   # B4
```

---

## Section C — Test Suite (All Green)

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| C1 | Smoke tests pass | `make smoke` | 165 passed | [ ] |
| C2 | Workflow integration tests pass | `python -m pytest tests/workflows/test_escort_payment_flow.py -m workflow --timeout=60 -q` | 59 passed | [ ] |
| C3 | No import errors | `python -c "from app.main import app; print('OK')"` | `OK` | [ ] |

```bash
cd /home/azim/core && source /home/azim/.venv/bin/activate

# C1
make smoke 2>&1 | tail -3

# C2
python -m pytest tests/workflows/test_escort_payment_flow.py -m workflow \
  --timeout=60 -q 2>&1 | tail -3

# C3
python -c "from app.main import app; print('OK')"
```

---

## Section D — Infrastructure Health

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| D1 | App responds to health | `curl -sf http://localhost:8200/health` | `{"status":"ok"}` | [ ] |
| D2 | Deep health all green | `curl -sf -H "X-Internal-Key: <key>" http://localhost:8200/health/deep` | `ok: true` | [ ] |
| D3 | PostgreSQL accepting connections | `docker exec ai-postgres psql -U postgres -c "SELECT 1;"` | `(1 row)` | [ ] |
| D4 | Redis responding | `docker exec ai-redis redis-cli PING` | `PONG` | [ ] |
| D5 | Ollama available | `curl -sf http://localhost:11434/api/tags` | JSON response | [ ] |
| D6 | Bridge2 (OPS) healthy | `curl -sf http://localhost:8081/health` | JSON response | [ ] |
| D7 | Bridge1 (HR) healthy | `curl -sf http://localhost:8082/health` | JSON response | [ ] |

```bash
# D1 + D2
curl -sf http://localhost:8200/health
curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/health/deep | python3 -m json.tool

# D3
docker exec ai-postgres psql -U postgres -c "SELECT 1;"

# D4
docker exec ai-redis redis-cli PING

# D5
curl -sf http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print('Ollama OK, models:', [m['name'] for m in d.get('models',[])])"

# D6 + D7
curl -sf http://localhost:8081/health
curl -sf http://localhost:8082/health
```

---

## Section E — Resource Baseline

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| E1 | Disk space >= 5 GB free | `df -h /home` | >= 5G available | [ ] |
| E2 | Memory < 80% used | `free -h` | < 80% | [ ] |
| E3 | DB connection pool healthy | `docker exec ai-postgres psql -U postgres -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"` | No `idle in transaction` | [ ] |
| E4 | Outbound queue depth < 100 | `curl -H "X-Internal-Key: <key>" http://localhost:8200/scheduler/status` | queue_depth < 100 | [ ] |
| E5 | No old DLQ messages | See command below | dlq_count = 0 or known/expected | [ ] |

```bash
# E1
df -h /home | awk 'NR==2 {print $4 " available on /home"}'

# E2
free -h | awk 'NR==2 {printf "RAM: %s used of %s\n", $3, $2}'

# E3
docker exec ai-postgres psql -U postgres -c \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY state ORDER BY state;"

# E4 + E5
curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/scheduler/status \
  | python3 -m json.tool

# DLQ count
docker exec ai-postgres psql -U postgres -d postgres -c \
  "SELECT COUNT(*) as dlq_count FROM fazle_outbound_queue WHERE status='dead';"
```

---

## Section F — Monitoring Enabled

| # | Check | Command | Expected | Status |
|---|---|---|---|---|
| F1 | Prometheus scraping metrics | `curl -sf http://localhost:9090/-/healthy` | `Prometheus Server is Healthy.` | [ ] |
| F2 | Grafana accessible | `curl -sf -o /dev/null -w "%{http_code}" http://localhost:3030/login` | `200` | [ ] |
| F3 | Loki accessible | `curl -sf http://localhost:3100/ready` | `ready` | [ ] |
| F4 | App metrics endpoint works | `curl -sf http://localhost:8200/metrics` | Prometheus text format | [ ] |

```bash
curl -sf http://localhost:9090/-/healthy
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:3030/login
curl -sf http://localhost:3100/ready
curl -sf http://localhost:8200/metrics | head -5
```

---

## Section G — Rollback Readiness

| # | Check | Status |
|---|---|---|
| G1 | RECOVERY.md has been read and understood | [ ] |
| G2 | Pre-chaos backup path noted: `/home/azim/backups/fazle/pre_chaos_<TIMESTAMP>.dump` | [ ] |
| G3 | Can confirm: `docker exec ai-postgres pg_restore --list /tmp/verify.dump > /dev/null` passes | [ ] |
| G4 | Git tag created and visible: `git tag -l | grep pre-chaos` | [ ] |
| G5 | Know how to hard-stop all chaos scripts: `kill $(jobs -p)` or `systemctl stop fazle-core` | [ ] |

---

## GO / NO-GO Decision

| Section | Items | Green | Decision |
|---|---|---|---|
| A — Backup | 5 | ___/5 | |
| B — Git state | 4 | ___/4 | |
| C — Tests | 3 | ___/3 | |
| D — Infrastructure | 7 | ___/7 | |
| E — Resources | 5 | ___/5 | |
| F — Monitoring | 4 | ___/4 | |
| G — Rollback | 5 | ___/5 | |

**GO criteria:** All 33 items green.

**NO-GO conditions (hard blockers):**
- Any test in Section C failing
- App health (D1) failing
- DB not accepting connections (D3)
- Disk space < 2 GB free (E1)
- Pre-chaos backup not verified (A2)

**Signed off by:** _______________  
**Date/Time:** _______________  
**DECISION:** [ ] GO   [ ] NO-GO
