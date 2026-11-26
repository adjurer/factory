#!/bin/sh
# 자체 검증. 맥서버에서 클론한 직후 한 번 돌려 보면 된다.
#   bash watchdog/selftest.sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${PYTHON:-python3}
[ -x /opt/homebrew/bin/python3 ] && [ -z "${PYTHON:-}" ] && PY=/opt/homebrew/bin/python3
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
NOW=2026-11-27T07:00:00+09:00
fail() { echo "FAIL: $*" >&2; exit 1; }

# 1) 문법
"$PY" -m py_compile "$HERE/agent_watchdog.py" || fail "python 문법"
echo "ok  python 문법"

# 2) plist
if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$HERE/ai.yuri.agent-watchdog.plist" >/dev/null || fail "plist"
else
    "$PY" - "$HERE/ai.yuri.agent-watchdog.plist" <<'EOF' || exit 1
import sys, xml.etree.ElementTree as ET
ET.parse(sys.argv[1])
EOF
fi
echo "ok  plist XML"

# 3) 한 바퀴 (dry-run): 기대한 카드만, 기대한 단계로
OUT=$("$PY" "$HERE/agent_watchdog.py" --once --dry-run \
      --board "$HERE/test_board.json" --state "$T/state.json" --log "$T/events.jsonl" \
      --now "$NOW" --max-per-run 10)
printf '%s\n' "$OUT" > "$T/dry.txt"
GOT=$("$PY" - "$T/dry.txt" <<'EOF'
import re, sys
hit = []
for line in open(sys.argv[1], encoding='utf-8'):
    m = re.match(r'DRY \[(\w+)\] (\S+) ', line)
    if m:
        hit.append('%s=%s' % (m.group(2), m.group(1)))
print(' '.join(sorted(hit)))
EOF
)
EXPECT="T-1001=warn T-1004=escalate T-1005=warn T-1007=escalate T-1009=warn"
[ "$GOT" = "$EXPECT" ] || fail "잡은 카드가 다르다: [$GOT] 기대 [$EXPECT]"
echo "ok  dry-run 5건: $GOT"

# 4) 중복 방지: 같은 판·같은 시각으로 두 번 돌리면 두 번째는 조용해야 한다.
"$PY" "$HERE/agent_watchdog.py" --once \
    --board "$HERE/test_board.json" --state "$T/state2.json" --log "$T/events2.jsonl" \
    --now "$NOW" --max-per-run 10 --send-cmd "cat >> $T/sent.txt" >/dev/null
FIRST=$(grep -c '^:' "$T/sent.txt" 2>/dev/null || true)
[ -n "$FIRST" ] || FIRST=0
[ "$FIRST" -eq 5 ] || fail "첫 회차 전송 수 $FIRST (기대 5)"
echo "ok  첫 회차 전송 5건"
"$PY" "$HERE/agent_watchdog.py" --once \
    --board "$HERE/test_board.json" --state "$T/state2.json" --log "$T/events2.jsonl" \
    --now "$NOW" --max-per-run 10 --send-cmd "cat >> $T/sent2.txt" >/dev/null
[ -s "$T/sent2.txt" ] && fail "두 번째 회차에 또 보냈다(중복 방지 실패)"
echo "ok  두 번째 회차 전송 0건"

# 4-1) 전송 실패(토큰 없음)는 단계를 올리지 않고 5분 뒤 재시도한다.
mkdir -p "$T/nohome"
env -u SLACK_BOT_TOKEN -u SLACK_TOKEN HOME="$T/nohome" "$PY" "$HERE/agent_watchdog.py" --once \
    --board "$HERE/test_board.json" --state "$T/retry.json" --log "$T/retry.jsonl" \
    --now "$NOW" --max-per-run 10 >/dev/null 2>&1 || fail "실패 시나리오에서 죽었다"
"$PY" - "$T/retry.json" <<'EOF' || exit 1
import json, sys
st = json.load(open(sys.argv[1], encoding='utf-8'))['tasks']
assert st, 'state empty'
for tid, e in st.items():
    assert e.get('stage') == 0, (tid, e)
    assert e.get('attempts') == 1, (tid, e)
EOF
OUT2=$(env -u SLACK_BOT_TOKEN -u SLACK_TOKEN HOME="$T/nohome" "$PY" "$HERE/agent_watchdog.py" --once \
    --board "$HERE/test_board.json" --state "$T/retry.json" --log "$T/retry.jsonl" \
    --now "$NOW" --max-per-run 10 --verbose)
printf '%s' "$OUT2" | grep -q '재시도 대기' || fail "즉시 재시도했다(간격 무시)"
env -u SLACK_BOT_TOKEN -u SLACK_TOKEN HOME="$T/nohome" "$PY" "$HERE/agent_watchdog.py" --once \
    --board "$HERE/test_board.json" --state "$T/retry.json" --log "$T/retry.jsonl" \
    --now "$NOW" --max-per-run 10 --retry-sec 0 >/dev/null 2>&1
"$PY" - "$T/retry.json" <<'EOF' || exit 1
import json, sys
st = json.load(open(sys.argv[1], encoding='utf-8'))['tasks']
assert all(e.get('attempts') == 2 for e in st.values()), st
EOF
echo "ok  실패는 단계를 안 올리고 재시도한다"

# 5) 진전이 있으면 다시 찔러야 한다: T-1001 의 updated_at 만 새로 준 판
"$PY" - "$HERE/test_board.json" "$T/bumped.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
for t in d['tasks']:
    if t['id'] == 'T-1001':
        t['updated_at'] = '2026-11-27T06:45:00+09:00'   # 15분 전 -> 기준 미달
json.dump(d, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
EOF
"$PY" "$HERE/agent_watchdog.py" --once --dry-run \
    --board "$T/bumped.json" --state "$T/state2.json" --log "$T/events2.jsonl" \
    --now "$NOW" --max-per-run 10 | grep -q 'T-1001' && fail "진전 있는 카드를 또 찔렀다"
echo "ok  진전 있으면 조용"

# 6) 상태 정리: 끝난 카드는 상태 파일에서 사라져야 한다.
"$PY" - "$HERE/test_board.json" "$T/closed.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
for t in d['tasks']:
    if t['id'] in ('T-1001', 'T-1004', 'T-1005', 'T-1007', 'T-1009'):
        t['status'] = 'done'
json.dump(d, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
EOF
"$PY" "$HERE/agent_watchdog.py" --once \
    --board "$T/closed.json" --state "$T/state2.json" --log "$T/events2.jsonl" \
    --now "$NOW" --send-cmd "cat >> $T/sent3.txt" >/dev/null
"$PY" - "$T/state2.json" <<'EOF' || exit 1
import json, sys
st = json.load(open(sys.argv[1], encoding='utf-8'))
assert st.get('tasks') == {}, st.get('tasks')
EOF
echo "ok  끝난 카드 상태 정리"

# 7) 작업판이 없어도 죽지 않는다.
"$PY" "$HERE/agent_watchdog.py" --once --board "$T/nope.json" --state "$T/s.json" \
    --log "$T/e.jsonl" >/dev/null || fail "board 없을 때 죽었다"
echo "ok  board 없음 -> 정상 종료"

echo "전부 통과."
