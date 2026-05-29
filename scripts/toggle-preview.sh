#!/usr/bin/env bash
# toggle-preview.sh — One-tab control panel for fazle-core draft preview mode.
#
# Draft Preview = AUTO_REPLY_ENABLED=false (safe mode):
#   inbound replies are saved as drafts for admin APPROVE/REJECT
#   instead of being auto-sent.
#
# Usage:
#   ./scripts/toggle-preview.sh            # interactive menu
#   ./scripts/toggle-preview.sh enable     # turn ON  draft preview (safe mode)
#   ./scripts/toggle-preview.sh disable    # turn OFF draft preview (auto-reply)
#   ./scripts/toggle-preview.sh restart    # restart service
#   ./scripts/toggle-preview.sh status     # show current state

set -euo pipefail

ENV_FILE="/home/azim/core/.env"
SERVICE="fazle-core.service"
HEALTH_URL="http://127.0.0.1:8200/health"
SUDO_PW="Jahanalo@2019"

KEY="AUTO_REPLY_ENABLED"

c_red()    { printf "\033[31m%s\033[0m" "$1"; }
c_green()  { printf "\033[32m%s\033[0m" "$1"; }
c_yellow() { printf "\033[33m%s\033[0m" "$1"; }
c_cyan()   { printf "\033[36m%s\033[0m" "$1"; }

current_value() {
    grep -E "^${KEY}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' || echo ""
}

set_value() {
    local newval="$1"
    if grep -qE "^${KEY}=" "$ENV_FILE"; then
        sed -i "s|^${KEY}=.*|${KEY}=${newval}|" "$ENV_FILE"
    else
        printf "\n%s=%s\n" "$KEY" "$newval" >> "$ENV_FILE"
    fi
}

restart_service() {
    echo -n "Restarting ${SERVICE} ... "
    echo "$SUDO_PW" | sudo -S -p '' systemctl restart "$SERVICE" 2>/dev/null
    sleep 6
    if systemctl is-active --quiet "$SERVICE"; then
        c_green "active"; echo
    else
        c_red "FAILED"; echo
        return 1
    fi
    echo -n "Health check: "
    local body
    body="$(curl -s "$HEALTH_URL" || true)"
    if echo "$body" | grep -q '"status":"ok"'; then
        c_green "ok"; echo
    else
        c_red "BAD"; echo "  $body"
    fi
}

show_status() {
    local v ar mode
    v="$(current_value)"
    ar="${v:-<unset>}"
    if [[ "$v" == "false" ]]; then
        mode="$(c_yellow 'DRAFT PREVIEW (safe mode)')"
    elif [[ "$v" == "true" ]]; then
        mode="$(c_green 'AUTO-REPLY (live)')"
    else
        mode="$(c_red 'UNKNOWN')"
    fi
    echo "─────────────────────────────────────────────"
    echo " Service : $(systemctl is-active "$SERVICE" 2>/dev/null || echo 'inactive')"
    echo " ${KEY}=${ar}"
    echo " Mode    : ${mode}"
    echo "─────────────────────────────────────────────"
}

enable_preview() {
    echo "Enabling DRAFT PREVIEW (AUTO_REPLY_ENABLED=false) ..."
    set_value "false"
    restart_service
    show_status
}

disable_preview() {
    echo "Disabling DRAFT PREVIEW (AUTO_REPLY_ENABLED=true → live auto-reply) ..."
    set_value "true"
    restart_service
    show_status
}

menu() {
    while true; do
        clear
        echo "╔═════════════════════════════════════════════╗"
        echo "║   fazle-core — Draft Preview Control Tab    ║"
        echo "╚═════════════════════════════════════════════╝"
        show_status
        echo
        echo "  1) Enable  draft preview (safe mode)"
        echo "  2) Disable draft preview (live auto-reply)"
        echo "  3) Restart service only"
        echo "  4) Show status"
        echo "  q) Quit"
        echo
        read -rp "Choose: " ch
        case "$ch" in
            1) enable_preview;  read -rp "Press Enter..." ;;
            2) disable_preview; read -rp "Press Enter..." ;;
            3) restart_service; read -rp "Press Enter..." ;;
            4) read -rp "Press Enter..." ;;
            q|Q) exit 0 ;;
            *) ;;
        esac
    done
}

case "${1:-menu}" in
    enable)  enable_preview ;;
    disable) disable_preview ;;
    restart) restart_service ;;
    status)  show_status ;;
    menu|"") menu ;;
    *) echo "Usage: $0 [enable|disable|restart|status|menu]"; exit 2 ;;
esac
