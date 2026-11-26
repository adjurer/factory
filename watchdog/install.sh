#!/bin/sh
# 정체 감시 장치 설치/해제.
#   bash install.sh              설치 (plist 복사 + launchctl bootstrap + 즉시 1회)
#   bash install.sh uninstall    해제 (launchd 에서 내리고 plist 삭제, 상태·로그는 남긴다)
set -eu

LABEL="ai.yuri.agent-watchdog"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SCRIPT="$HERE/agent_watchdog.py"
PLIST_SRC="$HERE/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/agent-watchdog.log"
UID_N=$(id -u)

if [ "${1:-}" = "uninstall" ]; then
    launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "해제: $LABEL (상태 ~/.agent-watchdog, 로그 ~/Library/Logs/agent-watchdog.log 는 남겨 뒀다)"
    exit 0
fi

# --- 파이썬: 맥서버 기본은 홈브루. 없으면 시스템 파이썬.
PY=/opt/homebrew/bin/python3
[ -x "$PY" ] || PY=/usr/local/bin/python3
[ -x "$PY" ] || PY=/usr/bin/python3
if ! "$PY" -c 'import json,urllib.request' 2>/dev/null; then
    echo "파이썬을 못 쓴다: $PY" >&2
    exit 1
fi

[ -f "$SCRIPT" ] || { echo "agent_watchdog.py 가 없다: $SCRIPT" >&2; exit 1; }
[ -f "$PLIST_SRC" ] || { echo "plist 가 없다: $PLIST_SRC" >&2; exit 1; }
chmod +x "$SCRIPT" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$HOME/.agent-watchdog"

# plist 안의 기본 경로를 이 기기의 실제 경로로 바꿔 넣는다.
sed -e "s|/opt/homebrew/bin/python3|$PY|" \
    -e "s|/Users/tbd/factory/watchdog/agent_watchdog.py|$SCRIPT|" \
    -e "s|/Users/tbd/Library/Logs/agent-watchdog.log|$LOG|" \
    "$PLIST_SRC" > "$PLIST_DST"

if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$PLIST_DST" >/dev/null || { echo "plist 가 깨졌다: $PLIST_DST" >&2; exit 1; }
fi

launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
if ! launchctl bootstrap "gui/$UID_N" "$PLIST_DST" 2>/dev/null; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load -w "$PLIST_DST"
fi
launchctl kickstart -k "gui/$UID_N/$LABEL" 2>/dev/null || true

sleep 2
echo "설치: $LABEL → $PLIST_DST (python $PY)"
launchctl print "gui/$UID_N/$LABEL" 2>/dev/null | sed -n '1p;/state = /p;/last exit code/p' || true
echo "--- $LOG (마지막 5줄) ---"
tail -5 "$LOG" 2>/dev/null || echo "(아직 로그 없음)"
echo "확인: tail -f $LOG   /   $PY $SCRIPT --once --dry-run"
