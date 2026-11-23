# macmini-codex-bot

> **⚖️ 헌법 먼저: [`../CONSTITUTION.md`](../CONSTITUTION.md)** — 이 봇이 무엇을 왜 하는지는 헌법에 있고, 작업 절차는 [`AGENTS.md`](AGENTS.md)에 있습니다.

맥미니에서 **상시 백그라운드**로 돌면서 발사대 숏폼·그루테크 블로그의 무거운 일을 대신합니다.
유리(Pickle)는 하루 1~2회 `reports/`·`drafts/`만 읽고 판단·검수·게시만 합니다.

| 맡는 쪽 | 하는 일 |
|---|---|
| **맥미니 (이 봇)** | 핫딜·쿠팡 API 소싱, 원본 클립 다운로드·720x1280 인코딩·프레임 검사, 훅 카피·구매판단표·블로그 스펙 초안, 결과 보고 커밋·푸시 |
| **유리 (Pickle)** | 보고·초안 읽기, 규칙(`AGENTS.md`·`feed/SCHEMA.md`) 재귀 개선, 원격 게시·최종 검수·경원 님 결정 |

- 글감 원본: `../feed/` (스키마는 [`../feed/SCHEMA.md`](../feed/SCHEMA.md))
- Codex 작업 규칙: [`AGENTS.md`](AGENTS.md)
- 이 저장소는 **PUBLIC**입니다 — 키·비밀번호·개인정보는 절대 커밋하지 않습니다(`.env`는 `.gitignore`).

---

## 0. 원클릭 셋업 (권장)

맥미니 터미널에 아래 **두 줄**을 그대로 붙여넣으면 끝입니다.

```bash
mkdir -p ~/factory && git clone https://github.com/adjurer/factory.git ~/factory 2>/dev/null || git -C ~/factory pull --rebase
bash ~/factory/macmini-codex-bot/setup_macmini.sh
```

셋업이 하는 일:

1. `python3` venv(`.venv`) + `requirements.txt` 설치 (현재는 표준 라이브러리만)
2. `.env.example` → `.env` 복사(이미 있으면 보존) + 비어 있는 키 안내
3. `codex` CLI·로그인, `ffmpeg`·`yt-dlp`, push 권한 점검 (없으면 설치 방법 안내)
4. `launchd` 3종을 실제 경로로 `~/Library/LaunchAgents/` 에 등록
   (`codex-sync` 30분 · `hotdeal-radar` 15분 · `codex-report` 30분)
5. dry-run 자체 점검 1회 + 상태 표 출력

옵션: `--check`(점검만) · `--dry-run`(할 일만) · `--install-deps`(brew/npm 설치 시도) ·
`--no-launchd` · `--skip-tests` · `--dir ~/factory`. 반복 실행해도 안전합니다.

> 한 번 더 돌리는 버전: `git -C ~/factory pull -q --rebase && bash ~/factory/macmini-codex-bot/setup_macmini.sh`

### `.env` 에 넣을 것

```bash
open -e ~/factory/macmini-codex-bot/.env
```

| 키 | 어디서 | 없으면 |
|---|---|---|
| `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` | 쿠팡 파트너스 → 도구 → Open API 관리 | 단축 링크·골드박스 소싱 건너뜀 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | @BotFather | 텔레그램 알림 없음 |
| `DISCORD_WEBHOOK_URL` | 채널 설정 → 연동 → 웹훅 | 디스코드 알림 없음 |
| `X_WEBHOOK_URL` | Zapier/Make/n8n 릴레이 | X 알림 없음 |

키가 하나도 없어도 나머지는 정상 동작합니다(없는 기능만 조용히 건너뜀).
`codex` 는 구독 계정으로 `codex login` 한 번 필요합니다.

---

## 1. 수동 설치 (셋업 스크립트를 안 쓸 때)

```bash
xcode-select --install                      # python3, git (이미 있으면 건너뜀)
mkdir -p ~/factory && cd ~/factory
git clone https://github.com/adjurer/factory.git .
cd macmini-codex-bot
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
mkdir -p logs state output
cp .env.example .env && open -e .env
npm i -g @openai/codex && codex login       # 초안 자동 생성용
brew install ffmpeg yt-dlp                  # 클립 준비용
```

## 2. 수동 테스트

```bash
cd ~/factory/macmini-codex-bot
.venv/bin/python scripts/coupang_api.py check                 # 키 설정 확인(네트워크 없음)
.venv/bin/python scripts/codex_sync.py --dry-run              # 새 글감 초안만
.venv/bin/python scripts/hotdeal_radar.py --dry-run           # 핫딜 미리보기
.venv/bin/python scripts/clip_prep.py --check                 # ffmpeg/yt-dlp 확인
.venv/bin/python scripts/report_cycle.py --dry-run --no-pull  # 보고서 본문만
```

`output/20261120_1700/` 같은 폴더에 `shorts_package.md`, `captions.srt`, `codex_prompt.md`가 생기면 정상입니다.

실제 처리(Codex 호출 + `drafts/` 푸시):

```bash
.venv/bin/python scripts/codex_sync.py --run-codex
.venv/bin/python scripts/codex_sync.py --dry-run --id 20261123_0800
.venv/bin/python scripts/report_cycle.py            # 보고 → reports/ 커밋·푸시
```

## 3. launchd 등록 (상시 실행)

`setup_macmini.sh` 가 아래를 자동으로 합니다. 수동으로 할 때:

```bash
BOT="$HOME/factory/macmini-codex-bot"
PY="$BOT/.venv/bin/python3"          # venv 안 쓰면 /usr/bin/python3
cd "$BOT"

for f in launchd/*.plist; do
  sed -e "s|__BOT_DIR__|$BOT|g" -e "s|__PYTHON__|$PY|g" "$f" > "$HOME/Library/LaunchAgents/$(basename "$f")"
done
plutil -lint ~/Library/LaunchAgents/com.gurutech.*.plist

for label in com.gurutech.codex-sync com.gurutech.hotdeal-radar com.gurutech.codex-report; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$label.plist"
done
```

| 에이전트 | 주기 | 하는 일 |
|---|---|---|
| `com.gurutech.codex-sync` | 30분 | `git pull` → 새 글감 → `output/{id}/` 초안 (+codex) → `drafts/` 커밋·푸시 |
| `com.gurutech.hotdeal-radar` | 15분 | 핫딜 RSS → 키워드 필터 → 중복 제거 → 알림 |
| `com.gurutech.codex-report` | 매시 :05·:35 | `pull --rebase` → 상태·오류 수집 → `reports/latest.md` → 커밋·푸시 |

```bash
launchctl kickstart -k gui/$(id -u)/com.gurutech.codex-report   # 즉시 1회
launchctl print gui/$(id -u)/com.gurutech.codex-report | head -20
tail -f logs/codex-sync.log logs/hotdeal-radar.log logs/codex-report.log
```

해제:

```bash
for label in com.gurutech.codex-sync com.gurutech.hotdeal-radar com.gurutech.codex-report; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
```

> launchd는 `.env`를 자동으로 읽지 않습니다. 각 스크립트가 시작할 때 `macmini-codex-bot/.env`를 직접 파싱합니다
> (`scripts/common.py:load_dotenv`). plist에 적은 환경변수가 있으면 그쪽이 우선입니다.

## 4. 폴더 구조

```
macmini-codex-bot/
├── setup_macmini.sh         원클릭 셋업(venv·.env·도구점검·launchd·자체점검)
├── AGENTS.md                Codex 작업 규칙 — 역할 분담·쓰기 경계·절차
├── README.md                이 문서
├── .env.example             키 이름만(실제 값 금지)
├── requirements.txt         현재 표준 라이브러리만
├── config/feeds.json        핫딜 RSS 목록 + 키워드
├── scripts/
│   ├── common.py            .env·HTTP·JSON·로깅·잠금(repo_lock)
│   ├── coupang_api.py       쿠팡 파트너스 HMAC 서명·단축링크·골드박스·검색
│   ├── hotdeal_radar.py     핫딜 필터·중복제거·알림
│   ├── codex_sync.py        새 글감 → 초안 → drafts/ 커밋·푸시
│   ├── clip_prep.py         클립 720x1280 + 프레임 0/0.5/1.5초 검사
│   └── report_cycle.py      사이클 보고 → reports/ 커밋·푸시
├── launchd/                 LaunchAgent 템플릿 3개
├── reports/                 맥미니 → 유리 보고 (latest.md·daily·improvements.md)
├── drafts/                  맥미니가 만든 공유 초안 (candidates/{날짜}.md·{id}/)
├── state/                   seen.json·processed_ids.json (git 제외)
├── output/{글감 id}/        초안·클립 (git 제외)
└── logs/                    실행 로그 (git 제외)
```

## 5. 자주 나는 문제

| 증상 | 원인 / 해결 |
|---|---|
| launchd 등록 후 로그 없음 | `bash setup_macmini.sh --check` → `launchctl print gui/$(id -u)/com.gurutech.codex-report` |
| 보고가 GitHub에 안 올라감 | `git push --dry-run` 권한 없음 → `gh auth login` 또는 PAT/SSH 설정 |
| `codex: command not found` | `npm i -g @openai/codex` 후 `which codex`. plist PATH에 `/opt/homebrew/bin` 포함 |
| codex가 로그인 안 됨 | `codex login` (구독 계정). `~/.codex/auth.json` 생기면 정상 |
| `ffmpeg not found` | `brew install ffmpeg` (클립 준비만 영향) |
| 핫딜 알림 없음 | `.env` 웹훅 확인 → `python3 scripts/hotdeal_radar.py --dry-run -v` |
| 특정 피드 403/404 | `config/feeds.json`에서 `enabled:false` (사유 적힘) |
| `git pull` 실패 | 로컬 변경 확인 → `git -C ~/factory status`. 보고 스크립트는 실패해도 로컬로 계속 |
| 같은 글감 다시 만들기 | `state/processed_ids.json`에서 그 id 삭제 → `--dry-run` → `--run-codex` |
| 보고 주기가 겹침 | `state/.cycle.lock` 으로 자동 직렬화(겹치면 한 쪽이 건너뜀) |

## 6. 안전 수칙

- 이 봇은 **게시·결제·구매를 하지 않습니다.** 초안·패키지·보고까지만.
- 영상은 남의 크리에이터 것입니다. 재업로드 대신 직접 촬영 또는 허락+크레딧.
- `state/`, `logs/`, `.env`, `output/`, 영상 파일은 git 제외입니다.
- `adjurer`의 다른 저장소는 건드리지 않습니다.
