# macmini-codex-bot

맥미니에서 **상시 백그라운드**로 돌면서 두 가지를 합니다.
① 공개 핫딜 피드에서 IT·가성비 딜만 골라 알림, ② 새 글감(`../feed/`)이 올라오면 Codex용 숏폼·블로그·카드 초안을 만듭니다.

- 글감 원본: `../feed/` (스키마는 [`../feed/SCHEMA.md`](../feed/SCHEMA.md))
- Codex 작업 규칙: [`AGENTS.md`](AGENTS.md)
- 이 저장소는 **PUBLIC**입니다 — 키·비밀번호·개인정보는 절대 커밋하지 않습니다(`.env`는 `.gitignore` 처리됨).

---

## 0. 준비물

```bash
xcode-select --install          # python3, git (이미 있으면 건너뜀)
python3 --version               # 3.9 이상이면 OK
```

선택 사항:

```bash
# Codex CLI (자동 초안 생성을 실제로 돌릴 때)
npm i -g @openai/codex && codex login

# 영상 다시 받기(yt-dlp) — 링크된 클립은 저장소에 없습니다
brew install yt-dlp ffmpeg
```

## 1. 내려받기

```bash
mkdir -p ~/factory && cd ~/factory
git clone https://github.com/adjurer/factory.git .
cd macmini-codex-bot
mkdir -p logs state output
```

## 2. 환경변수

```bash
cp .env.example .env
open -e .env        # 값 입력 (쿠팡 키, 텔레그램/Discord 웹훅 중 원하는 것만)
```

키가 하나도 없어도 **전부 동작**합니다. 없는 기능만 조용히 건너뜁니다.

| 변수 | 없으면 |
|---|---|
| `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` | 단축 링크 변환 스킵(원본 링크 사용) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 알림 스킵 |
| `DISCORD_WEBHOOK_URL` | 디스코드 알림 스킵 |
| `X_WEBHOOK_URL` | X 알림 스킵(X는 웹훅이 없어 Zapier/Make/n8n 릴레이 주소를 넣습니다) |

> 쿠팡 키가 있어도 **핫딜 글 안에 쿠팡 상품 URL이 적혀 있을 때만** 파트너스 단축 링크로 바꿉니다.
> 제목으로 비슷한 상품을 검색해 붙이지 않습니다 — 다른 상품이 딜 상품처럼 보이면 안 되기 때문입니다.

## 3. 수동 테스트 (launchd 걸기 전에 한 번)

```bash
# 1) 핫딜 레이더 — 아무것도 보내지 않고 미리보기만
python3 scripts/hotdeal_radar.py --dry-run

# 2) 글감 동기화 — 초안 파일만 만들고 상태는 저장하지 않음
python3 scripts/codex_sync.py --dry-run

# 3) 쿠팡 키 확인(네트워크 호출 없음)
python3 scripts/coupang_api.py check
```

`output/20261120_1700/` 같은 폴더에 `shorts_package.md`, `captions.srt`, `codex_prompt.md`가 생기면 정상입니다.

실제 처리(Codex 호출 포함):

```bash
python3 scripts/codex_sync.py --run-codex
python3 scripts/codex_sync.py --dry-run --id 20261120_1700   # 특정 글감만
```

## 4. launchd에 등록 (상시 실행)

`launchd/*.plist` 의 `__BOT_DIR__` 를 실제 경로로 바꿔서 `~/Library/LaunchAgents` 에 넣습니다.

```bash
BOT="$HOME/factory/macmini-codex-bot"
cd "$BOT"

for f in launchd/com.gurutech.codex-sync.plist launchd/com.gurutech.hotdeal-radar.plist; do
  sed "s|__BOT_DIR__|$BOT|g" "$f" > "$HOME/Library/LaunchAgents/$(basename "$f")"
done

plutil -lint ~/Library/LaunchAgents/com.gurutech.codex-sync.plist      # OK 나오는지 확인
plutil -lint ~/Library/LaunchAgents/com.gurutech.hotdeal-radar.plist

launchctl bootout gui/$(id -u)/com.gurutech.codex-sync   2>/dev/null || true
launchctl bootout gui/$(id -u)/com.gurutech.hotdeal-radar 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gurutech.codex-sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gurutech.hotdeal-radar.plist
```

동작 확인:

```bash
launchctl kickstart -k gui/$(id -u)/com.gurutech.codex-sync    # 즉시 1회 실행
launchctl print gui/$(id -u)/com.gurutech.codex-sync | head -20
tail -f logs/codex-sync.log logs/hotdeal-radar.log
```

| 에이전트 | 주기 | 하는 일 |
|---|---|---|
| `com.gurutech.codex-sync` | 30분 | `git pull` → 새 글감 → `output/{id}/` 초안 (+codex) |
| `com.gurutech.hotdeal-radar` | 15분 | 핫딜 RSS → 키워드 필터 → 중복 제거 → 알림 |

해제:

```bash
launchctl bootout gui/$(id -u)/com.gurutech.codex-sync
launchctl bootout gui/$(id -u)/com.gurutech.hotdeal-radar
rm ~/Library/LaunchAgents/com.gurutech.codex-sync.plist ~/Library/LaunchAgents/com.gurutech.hotdeal-radar.plist
```

> launchd는 `.env`를 자동으로 읽지 않습니다. 스크립트가 시작할 때 `macmini-codex-bot/.env`를 직접 파싱해서
> 환경변수로 올리므로(`scripts/common.py:load_dotenv`) 별도 설정이 필요 없습니다.
> 단, launchd plist에 적은 환경변수가 있으면 그쪽이 우선입니다.

## 5. 폴더 구조 / 산출물

```
macmini-codex-bot/
├── AGENTS.md               Codex가 따르는 작업 규칙
├── .env.example
├── requirements.txt        (설치할 패키지 없음 — 표준 라이브러리만)
├── config/feeds.json       핫딜 RSS 목록 + 키워드(여기서 켜고 끔)
├── scripts/
│   ├── common.py           .env 로딩·HTTP·JSON·로그
│   ├── coupang_api.py      쿠팡 파트너스 HMAC 서명·단축링크·골드박스
│   ├── hotdeal_radar.py    핫딜 필터·중복제거·알림
│   └── codex_sync.py       새 글감 → Codex 작업물
├── launchd/                LaunchAgent 템플릿 2개
├── state/                  seen.json, processed_ids.json (git 제외)
├── output/{글감 id}/       manifest·codex_prompt·captions.srt·shorts_package·codex_output
└── logs/                   실행 로그 (git 제외)
```

## 6. 자주 나는 문제

| 증상 | 원인 / 해결 |
|---|---|
| `launchctl bootstrap` 후 아무 로그 없음 | `logs/` 폴더가 없거나 경로 오타. `launchctl print gui/$(id -u)/com.gurutech.codex-sync` 로 상태 확인 |
| `codex: command not found` | `npm i -g @openai/codex` 후 `which codex` 확인. plist의 PATH에 `/opt/homebrew/bin` 포함됨 |
| `python3: command not found` | `xcode-select --install`. plist는 `/usr/bin/python3` 사용 |
| 핫딜 알림이 안 옴 | `python3 scripts/hotdeal_radar.py --dry-run -v` 로 필터 단계 확인. `.env`의 웹훅 값 확인 |
| 특정 피드만 403/404 | `config/feeds.json`에서 `enabled`를 끄면 됩니다. 막힌 곳은 사유를 적어둠 |
| `git pull` 실패 | 저장소가 공개라 읽기는 되지만, push 권한이 없는 환경이면 `--no-pull` 로 로컬 feed 사용 |
| 같은 글감을 다시 만들고 싶다 | `state/processed_ids.json`에서 그 id 삭제 후 `--dry-run`(초안만) → `--run-codex` |

## 7. 안전 수칙

- 이 봇은 **게시·결제·구매를 하지 않습니다.** 초안과 알림까지만.
- 영상은 남의 크리에이터 것입니다. 그대로 재업로드하지 말고 직접 촬영(권장)이나 허락+크레딧을 쓰세요.
- `state/`, `logs/`, `.env`, `output/`은 `.gitignore` 대상입니다. 공개 저장소에 올라가는 건 코드와 글감 JSON뿐입니다.
