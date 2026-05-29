#!/usr/bin/env bash
# Unified runtime health check for Fazle Core + WhatsApp bridges + Meta channel signals.
# Usage: ./scripts/health_core_bridge_meta.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${CORE_DIR}/.env"
LOG_FILE="${CORE_DIR}/logs/fazle-core.log"

# Defaults (used if .env is missing a key)
APP_PORT="8200"
BRIDGE1_URL="http://localhost:8080"
BRIDGE2_URL="http://localhost:8081"
BRIDGE1_NUMBER=""
BRIDGE2_NUMBER=""
ADMIN_META_NUMBER=""
ADMIN_BRIDGE1_NUMBER=""
ADMIN_BRIDGE2_NUMBER=""
META_PHONE_NUMBER_ID=""
META_API_TOKEN=""
META_VERIFY_TOKEN=""

# Track failures while continuing checks.
OVERALL_FAIL=0
CORE_HTTP_OK=0
BRIDGE1_HTTP_OK=0
BRIDGE2_HTTP_OK=0
BRIDGE1_CODE="000"
BRIDGE2_CODE="000"

print_header() {
  printf "\n== %s ==\n" "$1"
}

pass() {
  printf "[PASS] %s\n" "$1"
}

warn() {
  printf "[WARN] %s\n" "$1"
}

fail() {
  printf "[FAIL] %s\n" "$1"
  OVERALL_FAIL=1
}

trim_quotes() {
  local v="$1"
  v="${v%\"}"
  v="${v#\"}"
  v="${v%\'}"
  v="${v#\'}"
  printf "%s" "$v"
}

load_env_key() {
  local key="$1"
  if [[ -f "$ENV_FILE" ]]; then
    local line
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
      trim_quotes "${line#*=}"
      return 0
    fi
  fi
  return 1
}

port_from_url() {
  local url="$1"
  local hostport
  hostport="$(printf "%s" "$url" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##')"
  if [[ "$hostport" == *:* ]]; then
    printf "%s" "${hostport##*:}"
  else
    printf "80"
  fi
}

port_listening_proc() {
  local port="$1"
  local hex
  hex="$(printf '%04X' "$port" | tr '[:lower:]' '[:upper:]')"
  awk -v p=":${hex}" 'NR > 1 { if ($2 ~ p"$") { found=1; exit } } END { exit(found ? 0 : 1) }' /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

is_port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"
    return $?
  fi

  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"
    return $?
  fi

  port_listening_proc "$port"
  return $?
}

http_code() {
  local url="$1"
  local code
  code="$(curl -sS -m 7 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || true)"
  if [[ -z "$code" || "$code" == "000" ]]; then
    printf "000"
  else
    printf "%s" "$code"
  fi
}

http_probe() {
  local label="$1"
  local url="$2"
  local code
  code="$(http_code "$url")"

  if [[ "$code" =~ ^2[0-9][0-9]$ || "$code" =~ ^3[0-9][0-9]$ ]]; then
    pass "$label reachable ($code) -> $url"
  elif [[ "$code" == "401" || "$code" == "403" ]]; then
    pass "$label reachable but auth-protected ($code) -> $url"
  else
    fail "$label unreachable ($code) -> $url"
  fi

  case "$label" in
    Core)
      if [[ "$code" =~ ^2[0-9][0-9]$ || "$code" =~ ^3[0-9][0-9]$ || "$code" == "401" || "$code" == "403" ]]; then
        CORE_HTTP_OK=1
      fi
      ;;
    Bridge1)
      BRIDGE1_CODE="$code"
      if [[ "$code" =~ ^2[0-9][0-9]$ || "$code" =~ ^3[0-9][0-9]$ || "$code" == "401" || "$code" == "403" ]]; then
        BRIDGE1_HTTP_OK=1
      fi
      ;;
    Bridge2)
      BRIDGE2_CODE="$code"
      if [[ "$code" =~ ^2[0-9][0-9]$ || "$code" =~ ^3[0-9][0-9]$ || "$code" == "401" || "$code" == "403" ]]; then
        BRIDGE2_HTTP_OK=1
      fi
      ;;
  esac
}

print_header "Config"

val="$(load_env_key APP_PORT || true)"; if [[ -n "$val" ]]; then APP_PORT="$val"; fi
val="$(load_env_key BRIDGE1_URL || true)"; if [[ -n "$val" ]]; then BRIDGE1_URL="$val"; fi
val="$(load_env_key BRIDGE2_URL || true)"; if [[ -n "$val" ]]; then BRIDGE2_URL="$val"; fi
val="$(load_env_key BRIDGE1_NUMBER || true)"; if [[ -n "$val" ]]; then BRIDGE1_NUMBER="$val"; fi
val="$(load_env_key BRIDGE2_NUMBER || true)"; if [[ -n "$val" ]]; then BRIDGE2_NUMBER="$val"; fi
val="$(load_env_key ADMIN_META_NUMBER || true)"; if [[ -n "$val" ]]; then ADMIN_META_NUMBER="$val"; fi
val="$(load_env_key ADMIN_BRIDGE1_NUMBER || true)"; if [[ -n "$val" ]]; then ADMIN_BRIDGE1_NUMBER="$val"; fi
val="$(load_env_key ADMIN_BRIDGE2_NUMBER || true)"; if [[ -n "$val" ]]; then ADMIN_BRIDGE2_NUMBER="$val"; fi
val="$(load_env_key META_PHONE_NUMBER_ID || true)"; if [[ -n "$val" ]]; then META_PHONE_NUMBER_ID="$val"; fi
val="$(load_env_key META_API_TOKEN || true)"; if [[ -n "$val" ]]; then META_API_TOKEN="$val"; fi
val="$(load_env_key META_VERIFY_TOKEN || true)"; if [[ -n "$val" ]]; then META_VERIFY_TOKEN="$val"; fi

printf "Core APP_PORT=%s\n" "$APP_PORT"
printf "Bridge1 URL=%s\n" "$BRIDGE1_URL"
printf "Bridge2 URL=%s\n" "$BRIDGE2_URL"
printf "Bridge1 Number=%s\n" "${BRIDGE1_NUMBER:-<empty>}"
printf "Bridge2 Number=%s\n" "${BRIDGE2_NUMBER:-<empty>}"
printf "Meta Admin Number=%s\n" "${ADMIN_META_NUMBER:-<empty>}"
printf "Bridge1 Admin Number=%s\n" "${ADMIN_BRIDGE1_NUMBER:-<empty>}"
printf "Bridge2 Admin Number=%s\n" "${ADMIN_BRIDGE2_NUMBER:-<empty>}"

print_header "Port Listeners"
if is_port_listening "$APP_PORT"; then
  pass "Port ${APP_PORT} is listening (core expected)"
else
  fail "Port ${APP_PORT} is not listening (core expected)"
fi

BRIDGE1_PORT="$(port_from_url "$BRIDGE1_URL")"
if is_port_listening "$BRIDGE1_PORT"; then
  pass "Port ${BRIDGE1_PORT} is listening (bridge1 expected)"
else
  fail "Port ${BRIDGE1_PORT} is not listening (bridge1 expected)"
fi

BRIDGE2_PORT="$(port_from_url "$BRIDGE2_URL")"
if is_port_listening "$BRIDGE2_PORT"; then
  pass "Port ${BRIDGE2_PORT} is listening (bridge2 expected)"
else
  fail "Port ${BRIDGE2_PORT} is not listening (bridge2 expected)"
fi

print_header "HTTP Health Endpoints"
http_probe "Core" "http://127.0.0.1:${APP_PORT}/health"
http_probe "Bridge1" "${BRIDGE1_URL}/health"
http_probe "Bridge2" "${BRIDGE2_URL}/health"

print_header "Bridge Endpoint Auto-Detect"

if [[ "$BRIDGE1_HTTP_OK" -eq 0 ]]; then
  for p in 8080 8081 8082; do
    candidate="http://127.0.0.1:${p}/health"
    c="$(http_code "$candidate")"
    if [[ "$c" =~ ^2[0-9][0-9]$ || "$c" =~ ^3[0-9][0-9]$ || "$c" == "401" || "$c" == "403" ]]; then
      warn "Bridge1 configured URL failed (${BRIDGE1_URL}/health -> ${BRIDGE1_CODE}), but reachable candidate found at ${candidate} (${c})"
      break
    fi
  done
fi

if [[ "$BRIDGE2_HTTP_OK" -eq 0 ]]; then
  for p in 8081 8080 8082; do
    candidate="http://127.0.0.1:${p}/health"
    c="$(http_code "$candidate")"
    if [[ "$c" =~ ^2[0-9][0-9]$ || "$c" =~ ^3[0-9][0-9]$ || "$c" == "401" || "$c" == "403" ]]; then
      if [[ "$candidate" != "${BRIDGE1_URL}/health" ]]; then
        warn "Bridge2 configured URL failed (${BRIDGE2_URL}/health -> ${BRIDGE2_CODE}), but reachable candidate found at ${candidate} (${c})"
      fi
      break
    fi
  done
fi

print_header "Meta Channel Config"

if [[ -n "$META_PHONE_NUMBER_ID" ]]; then
  pass "META_PHONE_NUMBER_ID is configured (${META_PHONE_NUMBER_ID})"
else
  fail "META_PHONE_NUMBER_ID is missing"
fi

if [[ -n "$META_API_TOKEN" ]]; then
  pass "META_API_TOKEN is configured"
else
  fail "META_API_TOKEN is missing"
fi

if [[ -n "$META_VERIFY_TOKEN" ]]; then
  pass "META_VERIFY_TOKEN is configured"
else
  fail "META_VERIFY_TOKEN is missing"
fi

print_header "Recent Webhook Activity (Log Signals)"

if [[ -f "$LOG_FILE" ]]; then
  log_meta_count="$(tail -n 800 "$LOG_FILE" | grep -cE 'POST /webhook/meta .* 200 OK' || true)"
  log_mcp1_count="$(tail -n 800 "$LOG_FILE" | grep -cE 'POST /webhook/mcp1 .* 200 OK' || true)"
  log_mcp2_count="$(tail -n 800 "$LOG_FILE" | grep -cE 'POST /webhook/mcp2 .* 200 OK' || true)"
  log_mtime="$(stat -c '%y' "$LOG_FILE" 2>/dev/null || echo 'unknown')"

  printf "Log file: %s\n" "$LOG_FILE"
  printf "Log mtime: %s\n" "$log_mtime"
  printf "meta webhook 200 count (tail 800): %s\n" "$log_meta_count"
  printf "mcp1 webhook 200 count (tail 800): %s\n" "$log_mcp1_count"
  printf "mcp2 webhook 200 count (tail 800): %s\n" "$log_mcp2_count"

  if [[ "$log_meta_count" -gt 0 ]]; then
    pass "Recent Meta webhook traffic detected"
  else
    warn "No recent Meta webhook 200 lines in last 800 log lines"
  fi

  if [[ "$log_mcp1_count" -gt 0 ]]; then
    pass "Recent Bridge1 (mcp1) webhook traffic detected"
  else
    warn "No recent Bridge1 (mcp1) webhook 200 lines in last 800 log lines"
  fi

  if [[ "$log_mcp2_count" -gt 0 ]]; then
    pass "Recent Bridge2 (mcp2) webhook traffic detected"
  else
    warn "No recent Bridge2 (mcp2) webhook 200 lines in last 800 log lines"
  fi
else
  warn "Core log file not found at ${LOG_FILE}; skipping webhook activity checks"
fi

print_header "Summary"
if [[ "$OVERALL_FAIL" -eq 0 ]]; then
  printf "Overall result: HEALTHY\n"
  exit 0
else
  printf "Overall result: UNHEALTHY\n"
  exit 1
fi
