# 정체 감시 장치 (agent watchdog)

작업판 `~/agent-hub/board.json` 의 `doing` 카드가 **20분** 조용하면 그 주인에게, **40분**이면 `#ai-lab` 에 찔러 올린다. 같은 카드의 같은 단계는 한 번만 보내고, 카드가 끝나면 잊는다.

- 설치: `cd ~/factory && git fetch origin watchdog/agent-watchdog && git checkout FETCH_HEAD -- watchdog && bash watchdog/install.sh`
- 확인: `tail -f ~/Library/Logs/agent-watchdog.log` · `python3 ~/factory/watchdog/agent_watchdog.py --once --dry-run`
- 해제: `bash ~/factory/watchdog/install.sh uninstall`

기준은 `--warn-min 15 --escalate-min 30` 또는 `~/.agent-watchdog/config.json`(`{"warn_min":15,"escalate_min":30,"channel":"C0CLN0CN4CX","owners":{"codex":"U0CM37GJN82"}}`)로 바꾼다. 누가 몇 번 멈췄는지는 `~/.agent-watchdog/events.jsonl`(JSON Lines)에서 `event`가 `warn`/`escalate` 인 줄을 센다. 상태 파일은 `~/.agent-watchdog/state.json`, 작업판 폴더에 `PAUSE` 가 있으면 아무 것도 보내지 않는다.
