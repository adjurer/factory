#!/usr/bin/env bash
# ============================================================================
#  macmini-codex-bot — 맥미니 원클릭 셋업
#
#  맥미니에서 이 스크립트 한 번이면 아래를 끝냅니다.
#    1) python3 venv(.venv) 만들고 requirements 설치
#    2) .env 준비 (.env.example 복사, 이미 있으면 보존) + 비어 있는 키 안내
#    3) codex CLI·ffmpeg·yt-dlp·push 권한 점검 (없으면 설치 안내)
#    4) launchd 3종(codex-sync·hotdeal-radar·codex-report)을 실제 경로로 등록
#    5) dry-run 자체 점검 1회 + 상태 표 출력
#
#  역할 분담 (설계 원칙)
#    · 맥미니(이 봇) = 무거운 실행 담당: 핫딜/쿠팡 API 소싱, 클립 다운로드·720x1280 인코딩,
#      후킹 카피·구매판단표·블로그 스펙 초안, reports/ 커밋·푸시.  → 사용량 걱정 없이 계속 돌림
#    · 유리(Pickle)  = 저비용 판단 담당: 하루 1~2회 reports/·drafts/만 읽고 규칙(AGENTS.md) 개선,
#      원격 게시·최종 검수·사용자 결정만 처리.  → 무거운 반복 작업을 유리가 하지 않게 함
#
#  사용법
#    bash setup_macmini.sh                 # 설치/갱신 (반복 실행해도 안전)
#    bash setup_macmini.sh --check         # 점검만, 아무것도 바꾸지 않음
#    bash setup_macmini.sh --dry-run       # 할 일만 출력
#    bash setup_macmini.sh --no-launchd    # launchd 등록 생략
#    bash setup_macmini.sh --skip-tests    # 자체 점검 생략
#    bash setup_macmini.sh --install-deps  # brew/npm으로 ffmpeg·yt-dlp·codex 설치 시도
#    bash setup_macmini.sh --dir ~/factory # 저장소 루트 직접 지정
#
#  ⚠️ 이 저장소(adjurer/factory)만 건드립니다. 키·개인정보는 .env 에만 두고 커밋하지 않습니다.
# ============================================================================
set -uo pipefail

SELF="${BASH_SOURCE[0]}"
BOT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_DIR="$(cd "$BOT_DIR/.." && pwd)"
CHECK_ONLY=0
DRY_RUN=0
USE_LAUNCHD=1
RUN_TESTS=1
INSTALL_DEPS=0
FORCE=0
PROBLEMS=0
WARNINGS=0
PY=""
PY_SOURCE=""

if [ -t 1 ]; then
  B='\033[1m'; DIM='\033[2m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; C='\033[36m'; N='\033[0m'
else
  B=''; DIM=''; G=''; Y=''; R=''; C=''; N=''
fi

say()  { printf "%b\n" "$*"; }
hdr()  { printf "\n%b\n" "${B}${C}== $* ==${N}"; }
ok()   { printf "  %b✓%b %s\n" "$G" "$N" "$*"; }
warn() { printf "  %b!%b %s\n" "$Y" "$N" "$*"; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf "  %b✗%b %s\n" "$R" "$N" "$*"; PROBLEMS=$((PROBLEMS + 1)); }
info() { printf "  %b·%b %s\n" "$DIM" "$N" "$*"; }

usage() {
  sed -n '2,30p' "$SELF" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --dry-run) DRY_RUN=1; CHECK_ONLY=1 ;;
    --no-launchd) USE_LAUNCHD=0 ;;
    --skip-tests) RUN_TESTS=0 ;;
    --install-deps) INSTALL_DEPS=1 ;;
    --force) FORCE=1 ;;
    --dir) shift; [ "$#" -gt 0 ] || { echo "--dir 값이 없습니다"; exit 2; }
           REPO_DIR="$(cd "$1" && pwd)" || exit 2; BOT_DIR="$REPO_DIR/macmini-codex-bot" ;;
    -h|--help) usage ;;
    *) echo "알 수 없는 옵션: $1 (--help 참고)"; exit 2 ;;
  esac
  shift
done

# 경로에 sed 구분자로 쓰는 문자가 있으면 치환 사고가 난다
case "$BOT_DIR$REPO_DIR$PY" in
  *'&'*|*'|'*) echo "[중단] 경로에 & 또는 | 가 있어 plist 치환을 안전하게 할 수 없습니다: $BOT_DIR"; exit 2 ;;
esac

say "${B}${C}macmini-codex-bot 셋업${N}  ${DIM}($([ "$CHECK_ONLY" = 1 ] && echo "점검 모드" || echo "설치 모드"))${N}"
say "  저장소: $REPO_DIR"
say "  봇 폴더: $BOT_DIR"
say "  맥미니가 무거운 실행(소싱·클립·초안·보고)을 맡고, 유리는 reports/·drafts/만 읽고 판단합니다."

# --------------------------------------------------------------------------- 1. 사전 점검
hdr "1/8 사전 점검"

if [ "$(uname -s)" = "Darwin" ]; then
  ok "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') · $(uname -m)"
elif [ "$FORCE" = 1 ]; then
  warn "macOS가 아닙니다($(uname -s)) — --force 로 계속합니다(launchd는 건너뜁니다)"
  USE_LAUNCHD=0
else
  bad "macOS가 아닙니다($(uname -s)). 맥미니에서 실행하세요. (테스트라면 --force)"
  exit 2
fi

if ! command -v git >/dev/null 2>&1; then
  bad "git 없음 → xcode-select --install"
  exit 2
fi
ok "git $(git --version | cut -d' ' -f3)"

if ! command -v python3 >/dev/null 2>&1; then
  bad "python3 없음 → xcode-select --install (또는 brew install python)"
  exit 2
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '0.0')"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  ok "python3 $PYVER ($(command -v python3))"
else
  bad "python3 $PYVER — 3.9 이상 필요"
  exit 2
fi

if [ ! -d "$BOT_DIR" ] || [ ! -f "$BOT_DIR/scripts/codex_sync.py" ]; then
  bad "$BOT_DIR 에 봇 파일이 없습니다. 저장소를 다시 클론하세요."
  exit 2
fi

if ! git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  bad "$REPO_DIR 가 git 저장소가 아닙니다"
  exit 2
fi

REMOTE_URL="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
case "$REMOTE_URL" in
  *adjurer/factory*) ok "origin: $REMOTE_URL" ;;
  "") bad "origin 원격이 없습니다 → git remote add origin https://github.com/adjurer/factory.git"; exit 2 ;;
  *) bad "origin이 adjurer/factory 가 아닙니다: $REMOTE_URL"
     say "     ${R}이 스크립트는 adjurer/factory 전용입니다. 다른 저장소는 건드리지 않습니다.${N}"; exit 2 ;;
esac

if [ -n "$(git -C "$REPO_DIR" status --porcelain 2>/dev/null | head -1)" ]; then
  warn "저장소에 커밋 안 된 변경이 있습니다(셋업은 건드리지 않고 그대로 둡니다)"
fi
info "xcode CLT: $(xcode-select -p 2>/dev/null || echo '없음 — xcode-select --install 권장(선택)')"

# --------------------------------------------------------------------------- 2. 저장소 최신화
hdr "2/8 저장소 최신화"
if [ "$CHECK_ONLY" = 1 ]; then
  info "건너뜀(점검 모드) — 현재 $(git -C "$REPO_DIR" log -1 --format='%h %s' 2>/dev/null | cut -c1-70)"
else
  if git -C "$REPO_DIR" pull --rebase --autostash -q 2>/dev/null; then
    ok "git pull --rebase 완료 — $(git -C "$REPO_DIR" log -1 --format='%h %s' | cut -c1-70)"
  else
    warn "git pull 실패 — 로컬 변경/충돌을 확인하세요: git -C \"$REPO_DIR\" status"
  fi
fi

# --------------------------------------------------------------------------- 3. 파이썬 venv
hdr "3/8 파이썬 venv"
VENV="$BOT_DIR/.venv"
if [ -x "$VENV/bin/python3" ]; then
  PY="$VENV/bin/python3"; PY_SOURCE="venv"
  ok "기존 venv 사용 — $("$PY" -V 2>&1)"
elif [ "$CHECK_ONLY" = 1 ]; then
  PY="$(command -v python3)"; PY_SOURCE="시스템"
  info "venv 없음 (설치 모드에서 만듭니다)"
else
  if python3 -m venv "$VENV" >/dev/null 2>&1 && [ -x "$VENV/bin/python3" ]; then
    PY="$VENV/bin/python3"; PY_SOURCE="venv"
    ok "venv 생성 — $("$PY" -V 2>&1)"
  else
    PY="$(command -v python3)"; PY_SOURCE="시스템"
    warn "venv 생성 실패 — 시스템 python3를 씁니다(설치할 패키지가 없어 동작에는 지장 없음)"
  fi
fi

if [ "$CHECK_ONLY" = 0 ] && [ "$PY_SOURCE" = "venv" ] && [ -f "$BOT_DIR/requirements.txt" ]; then
  if "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1; then :; fi
  if "$PY" -m pip install --quiet -r "$BOT_DIR/requirements.txt" >/dev/null 2>&1; then
    ok "의존성 설치 확인 (requirements.txt — 현재 표준 라이브러리만)"
  else
    warn "pip install 실패 — 오프라인이어도 표준 라이브러리만 쓰므로 동작합니다"
  fi
fi

if [ "$CHECK_ONLY" = 0 ]; then
  mkdir -p "$BOT_DIR/logs" "$BOT_DIR/state" "$BOT_DIR/output" 2>/dev/null || \
    warn "logs/state/output 폴더를 만들지 못했습니다"
fi

# --------------------------------------------------------------------------- 4. .env
hdr "4/8 .env (키)"
ENV_FILE="$BOT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  ok ".env 있음 — 기존 파일을 보존합니다"
elif [ "$CHECK_ONLY" = 1 ]; then
  warn ".env 없음 (설치 모드에서 .env.example 복사)"
else
  if cp "$BOT_DIR/.env.example" "$ENV_FILE" 2>/dev/null; then
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    ok ".env 생성 (.env.example 복사) — $ENV_FILE"
  else
    bad ".env.example 이 없습니다"
  fi
fi

env_has() {  # env_has NAME → 비어 있지 않으면 0
  [ -f "$ENV_FILE" ] || return 1
  local val
  val="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' " )"
  [ -n "$val" ]
}

MISSING_KEYS=""
for key in COUPANG_ACCESS_KEY COUPANG_SECRET_KEY; do
  if env_has "$key"; then ok "$key 설정됨"; else MISSING_KEYS="$MISSING_KEYS $key"; fi
done
for key in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID DISCORD_WEBHOOK_URL X_WEBHOOK_URL; do
  if env_has "$key"; then ok "$key 설정됨"; fi
done
if [ -n "$MISSING_KEYS" ]; then
  warn "필수 키 비어 있음:$MISSING_KEYS"
  say "     ${DIM}쿠팡 파트너스 > 도구 > Open API 관리 에서 Access/Secret Key 발급 → .env 에 붙여넣기${N}"
  say "     ${DIM}없어도 봇은 돌지만, 단축 링크·골드박스 소싱이 건너뛰어집니다.${N}"
fi
if ! (env_has TELEGRAM_BOT_TOKEN && env_has TELEGRAM_CHAT_ID) && ! env_has DISCORD_WEBHOOK_URL && ! env_has X_WEBHOOK_URL; then
  info "알림 채널 없음 → 핫딜은 미리보기만(알림 안 보냄). 원하면 .env 에 텔레그램/Discord 웹훅."
fi

# --------------------------------------------------------------------------- 5. 도구
hdr "5/8 실행 도구 (codex·ffmpeg·yt-dlp)"
have() { command -v "$1" >/dev/null 2>&1; }

if [ "$INSTALL_DEPS" = 1 ] && [ "$CHECK_ONLY" = 0 ]; then
  if have brew; then
    have ffmpeg || { say "  brew install ffmpeg …"; brew install ffmpeg >/dev/null 2>&1 || warn "ffmpeg 설치 실패"; }
    have yt-dlp || { say "  brew install yt-dlp …"; brew install yt-dlp >/dev/null 2>&1 || warn "yt-dlp 설치 실패"; }
  else
    warn "brew 없음 → ffmpeg·yt-dlp는 수동 설치 (https://brew.sh)"
  fi
  if ! have codex; then
    if have npm; then
      say "  npm i -g @openai/codex …"
      npm i -g @openai/codex >/dev/null 2>&1 || warn "codex 설치 실패 — 수동: npm i -g @openai/codex"
    else
      warn "npm 없음 → Node 설치 후 npm i -g @openai/codex"
    fi
  fi
fi

if have codex; then
  ok "codex $(codex --version 2>/dev/null | head -1 | cut -c1-60)"
  if [ -f "$HOME/.codex/auth.json" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    ok "codex 로그인 흔적 있음 (~/.codex/auth.json 또는 OPENAI_API_KEY)"
  else
    warn "codex 로그인 필요 → 터미널에서 'codex login' 한 번 (구독 계정으로)"
  fi
else
  warn "codex CLI 없음 → npm i -g @openai/codex && codex login (초안 자동 생성이 멈춤)"
fi

if have ffmpeg && have ffprobe; then
  ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
else
  warn "ffmpeg/ffprobe 없음 → brew install ffmpeg (720x1280 클립·프레임 검사 불가)"
fi
if have yt-dlp; then
  ok "yt-dlp $(yt-dlp --version 2>/dev/null)"
else
  warn "yt-dlp 없음 → brew install yt-dlp (원본 클립 다시 받기 불가)"
fi

# --------------------------------------------------------------------------- 6. git 신원·푸시 권한
hdr "6/8 git 신원·push 권한 (보고를 GitHub으로 돌려보내는 통로)"
if [ -z "$(git -C "$REPO_DIR" config user.email 2>/dev/null)" ]; then
  if [ "$CHECK_ONLY" = 1 ]; then
    warn "git user.email 미설정 (설치 모드에서 저장소 로컬로 설정)"
  else
    git -C "$REPO_DIR" config user.email "59545988+adjurer@users.noreply.github.com"
    git -C "$REPO_DIR" config user.name "macmini-codex-bot"
    ok "저장소 로컬 git 신원 설정 (noreply 주소 — 개인 메일 노출 없음)"
  fi
else
  ok "git user: $(git -C "$REPO_DIR" config user.name 2>/dev/null) <$(git -C "$REPO_DIR" config user.email 2>/dev/null)>"
fi

if [ "$CHECK_ONLY" = 1 ]; then
  info "push 권한 확인은 설치 모드에서 합니다(원격에 아무것도 쓰지 않는 dry-run)"
elif git -C "$REPO_DIR" push --dry-run origin "HEAD:refs/heads/main" >/dev/null 2>&1; then
  ok "push 권한 확인 — 보고(drafts/reports)를 자동으로 올릴 수 있습니다"
else
  warn "push 권한 없음 → 맥미니에서 아래 중 하나:"
  say "     ${DIM}1) gh auth login  (GitHub CLI, 권장)${N}"
  say "     ${DIM}2) Personal Access Token 으로 remote URL 설정${N}"
  say "     ${DIM}3) SSH 키 등록 후 remote를 git@github.com:adjurer/factory.git 로 변경${N}"
  say "     ${DIM}권한이 없어도 봇은 로컬에서 돌고 보고는 커밋만 쌓입니다(push는 나중에).${N}"
fi

# --------------------------------------------------------------------------- 7. launchd
hdr "7/8 launchd 등록"
LABELS="com.gurutech.codex-sync com.gurutech.hotdeal-radar com.gurutech.codex-report"
if [ "$USE_LAUNCHD" = 0 ]; then
  info "건너뜀 (--no-launchd 또는 macOS 아님)"
elif [ "$CHECK_ONLY" = 1 ]; then
  for label in $LABELS; do
    if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then ok "$label 등록됨"; else info "$label 미등록"; fi
  done
else
  AGENTS_DIR="$HOME/Library/LaunchAgents"
  mkdir -p "$AGENTS_DIR"
  for label in $LABELS; do
    src="$BOT_DIR/launchd/$label.plist"
    dst="$AGENTS_DIR/$label.plist"
    if [ ! -f "$src" ]; then
      bad "plist 없음: $src"
      continue
    fi
    sed -e "s|__BOT_DIR__|$BOT_DIR|g" -e "s|__REPO_DIR__|$REPO_DIR|g" -e "s|__PYTHON__|$PY|g" \
      "$src" > "$dst.tmp" && mv "$dst.tmp" "$dst"
    if command -v plutil >/dev/null 2>&1 && ! plutil -lint "$dst" >/dev/null 2>&1; then
      bad "plist 형식 오류: $dst (plutil -lint 로 확인)"
      continue
    fi
    launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
    if launchctl bootstrap "gui/$(id -u)" "$dst" >/dev/null 2>&1; then
      ok "$label 등록"
    elif launchctl load -w "$dst" >/dev/null 2>&1; then
      ok "$label 등록 (launchctl load)"
    else
      bad "$label 등록 실패 — launchctl print gui/$(id -u)/$label 로 확인"
    fi
  done
  info "주기: codex-sync 30분 · hotdeal-radar 15분 · codex-report 30분"
  info "로그: $BOT_DIR/logs/"
fi

# --------------------------------------------------------------------------- 8. 자체 점검
hdr "8/8 dry-run 자체 점검"
if [ "$RUN_TESTS" = 0 ] || [ "$CHECK_ONLY" = 1 ]; then
  info "건너뜀"
else
  SELFTEST="$BOT_DIR/output/_selftest"
  mkdir -p "$SELFTEST"

  if "$PY" "$BOT_DIR/scripts/coupang_api.py" check >"$SELFTEST/coupang_check.txt" 2>&1; then
    ok "쿠팡 키 점검 — $(head -1 "$SELFTEST/coupang_check.txt")"
  else
    warn "쿠팡 키 점검 실패 — $SELFTEST/coupang_check.txt"
  fi

  if "$PY" "$BOT_DIR/scripts/codex_sync.py" --dry-run --no-pull --limit 1 --out "$SELFTEST/output" \
      >"$SELFTEST/codex_sync.txt" 2>&1; then
    ok "codex_sync dry-run — $(tail -1 "$SELFTEST/codex_sync.txt" | cut -c1-80)"
  else
    warn "codex_sync dry-run 실패 — $SELFTEST/codex_sync.txt"
  fi

  if "$PY" "$BOT_DIR/scripts/report_cycle.py" --dry-run --no-pull \
      --reports-dir "$SELFTEST/reports" >"$SELFTEST/report.md" 2>"$SELFTEST/report.err"; then
    ok "report_cycle dry-run — $(grep -m1 '^| 글감 |' "$SELFTEST/report.md" | sed 's/^| //; s/ |$//')"
  else
    warn "report_cycle dry-run 실패 — $SELFTEST/report.err"
  fi

  if "$PY" "$BOT_DIR/scripts/hotdeal_radar.py" --dry-run --limit 1 >"$SELFTEST/hotdeal.txt" 2>&1; then
    ok "핫딜 레이더 dry-run — $(grep -E '핫딜 [0-9]+건|새로 알릴' "$SELFTEST/hotdeal.txt" | head -1)"
  else
    warn "핫딜 dry-run 실패(네트워크/피드 차단 가능) — $SELFTEST/hotdeal.txt"
  fi

  if have ffmpeg; then
    ok "clip_prep 점검 — ffmpeg 있음"
  else
    info "clip_prep — ffmpeg 없어 클립 준비는 건너뜁니다"
  fi
fi

# --------------------------------------------------------------------------- 요약
hdr "셋업 요약"
say "  ${B}다음 단계${N}"
say "  1) .env 확인: ${DIM}$ENV_FILE${N}"
say "  2) 로그: ${DIM}tail -f \"$BOT_DIR/logs/codex-sync.log\" \"$BOT_DIR/logs/codex-report.log\"${N}"
say "  3) 첫 보고를 바로 올리려면: ${DIM}cd \"$BOT_DIR\" && $PY scripts/report_cycle.py${N}"
say "  4) 상태: ${DIM}launchctl print gui/$(id -u)/com.gurutech.codex-report | head -20${N}"
say ""
say "  ${B}역할 분담${N}"
say "  · 맥미니 = 소싱(쿠팡 API·핫딜) · 클립 720x1280 준비 · 카피/구매판단표/블로그 초안 · reports 푸시"
say "  · 유리   = reports/·drafts/ 읽고 규칙 개선 · 최종 검수·원격 게시·사용자 결정"
say ""
if [ "$PROBLEMS" -gt 0 ]; then
  say "  ${R}문제 $PROBLEMS개${N} · 경고 $WARNINGS개 — 위 내용을 확인하세요."
  exit 1
elif [ "$WARNINGS" -gt 0 ]; then
  say "  ${Y}경고 $WARNINGS개${N} — 봇은 돌지만 키/도구를 채우면 기능이 늘어납니다."
  exit 0
else
  say "  ${G}모두 정상${N}"
  exit 0
fi
