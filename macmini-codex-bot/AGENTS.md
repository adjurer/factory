# AGENTS.md — 맥미니 Codex 작업 지시서

> **⚖️ 헌법 먼저: [`../CONSTITUTION.md`](../CONSTITUTION.md) — 이 문서보다 위에 있는 규칙이다. 매 주기 시작할 때 이 둘을 함께 읽는다.**
> 충돌하면 헌법이 이긴다. 이 문서를 고칠 수 있는 것은 유리뿐이고, 맥미니는 `reports/improvements.md`에 제안만 한다.

> 지시서 버전: **v2** (2026-11-23) · 이 파일은 **유리만 수정**합니다. 맥미니는 읽고, 개선 제안은 `reports/improvements.md`에만 씁니다.
> 프로젝트: `adjurer/factory` — 발사대 숏폼·그루테크 블로그의 **소싱·초안·검수를 맥미니가 대신 돌리는** 저장소.
> 돈·권리·발행이 걸린 일은 사람이 최종 확인합니다.

---

## 0. 역할 분담 — 이 문서의 첫 번째 규칙

경원 님의 지시(2026-11-23): **"유리(Pickle)의 사용량은 최소화하고, 맥미니 + OpenAI Codex 구독이 대부분의 무거운 작업을 반자동으로 처리한다."**

| | 맥미니 Codex (이 봇) | 유리 (Pickle) |
|---|---|---|
| 성격 | **무거운 실행 담당.** 구독 안에서 사용량 걱정 없이 계속 돌린다 | **저비용 판단 담당.** 하루 1~2회 또는 새 보고가 있을 때만 본다 |
| 소싱 | 쿠팡 파트너스 API(골드박스·검색)·핫딜 RSS로 3만~15만 원대 신박템/가성비 후보 수집 | 후보 중 게시할 것만 고르고, 경원 님 결정이 필요한 것은 물어본다 |
| 클립 | 원본 다운로드(yt-dlp) → 720x1280 인코딩 → 0/0.5/1.5초 프레임 검사 | 완성 클립·프레임만 눈으로 확인 |
| 카피 | 훅 3안·본문·첫 댓글·구매판단표·카드뉴스 문구 초안 | 톤·규칙 개선, 금지 표현 제거 |
| 블로그 | 스펙·가격 비교 데이터와 본문 초안 | 최종 검수, 발행 |
| 보고 | 실행 결과·오류·개선 제안을 `reports/latest.md`로 커밋·푸시 | 보고를 읽고 다음 지시를 정한다 |
| 금지 | SNS 게시·결제·구매·로그인 대행 | 맥미니가 할 수 있는 반복 작업을 직접 하지 않는다 |

**한 줄 원칙:** 맥미니가 할 수 있는 일은 맥미니가 끝까지 한다. 유리는 "판단"과 "경원 님께 물을 것"만 남긴다.

---

## 1. 쓰기 경계 (누가 어떤 파일을 쓰는가)

| 경로 | 쓰는 쪽 | 비고 |
|---|---|---|
| `macmini-codex-bot/reports/` | **맥미니** | `latest.md`, `daily/{날짜}.md`, `improvements.md` |
| `macmini-codex-bot/drafts/{id}/` | **맥미니** | 공유용 초안(스크립트·블로그·카드·구매판단표) |
| `macmini-codex-bot/drafts/candidates/` | **맥미니** | 소싱 후보 목록 |
| `macmini-codex-bot/output/`, `state/`, `logs/`, `.env` | 맥미니(로컬 전용) | **git 제외** — 영상·상태·키는 올리지 않는다 |
| `feed/{YYYY-MM-DD}/{id}.json` | 맥미니가 **완성 패키지**를 올릴 때 | `feed/SCHEMA.md` 필수 필드 전부 |
| `feed/index.json`, `feed/latest.json` | 손으로 고치지 않는다 | `python3 feed/tools/build_index.py` 로만 재생성 |
| `AGENTS.md`, `feed/SCHEMA.md`, `scripts/`, `setup_macmini.sh`, `launchd/`, `README.md` | **유리만** | 맥미니는 읽기 전용. 고칠 점은 `reports/improvements.md`에 |
| `adjurer`의 다른 저장소 | 아무도 | 이 저장소 밖은 읽지도 쓰지도 않는다 |

**푸시 범위:** `reports/`, `drafts/`, `feed/` 세 곳만. 그 밖의 변경이 작업 트리에 있으면 건드리지 말고 보고에 적는다.

```
drafts: {id} — 맥미니 Codex 초안
report: YYYY-MM-DD HH:MM 맥미니 사이클 — 글감 미처리 N · 초안 N · 실패 N
feed: {id} ({n}호) {상품명} — 맥미니 소싱
```

**충돌 규칙:** `git pull --rebase`가 `feed/index.json`에서 충돌하면 손으로 풀지 말고
`git checkout --theirs feed/index.json && python3 feed/tools/build_index.py` 로 재생성한다.
`reports/`·`drafts/` 충돌은 이 봇이 쓴 파일이므로 최신(내 쪽)을 남긴다.

---

## 2. 한 주기(30분) 절차

1. `git pull --rebase --autostash` — 최신 글감과 이 지시서를 받는다. 실패해도 로컬 데이터로 계속.
2. 이 파일(`AGENTS.md`)의 버전 줄과 SHA를 확인한다. 바뀌었으면 새 규칙이 우선이다.
3. `feed/index.json`에서 `state/processed_ids.json`에 없는 새 글감을 찾는다 → `codex_sync.py` 가 초안을 만든다.
4. **소싱**: `python3 scripts/coupang_api.py goldbox --limit 20` + `search` 로 후보를 모으고,
   `hotdeal_radar.py`가 잡은 핫딜과 합쳐 3만~15만 원대 신박템/가성비 후보를 3~5개 고른다.
5. **클립**: 후보 중 영상 소스가 확인된 것만 `python3 scripts/clip_prep.py --src <URL|파일> --id <id>` 로
   720x1280 클립과 0/0.5/1.5초 프레임을 만든다.
6. **카피·판단표**: `shorts_script.md`(훅 3안) + `hotdeal_card.md`(5장) + 구매판단표(가격·배송·리뷰·주의)를 쓴다.
7. **패키지 적재**: 새 세트는 `feed/{날짜}/{id}.json` 으로 쓰고 `build_index.py` 실행.
   블로그 데이터는 `drafts/{id}/blog_draft.md`(+ 스펙 표)로.
8. **보고**: `python3 scripts/report_cycle.py` — `reports/latest.md` 갱신 후 `reports/`·`drafts/` 커밋·푸시.

> 자동 실행은 launchd 3종이 맡는다: `codex-sync`(30분, 초안) · `hotdeal-radar`(15분, 알림) · `codex-report`(30분, 보고).
> 수동으로 한 번 돌릴 때도 위 순서를 지킨다.

---

## 3. 소싱 기준

- **가격대: 3만~15만 원** (그 밖은 "경원 님 확인 필요"로 표시만).
- **결**: 신박템·생활가전·테크가젯·가성비. 리뷰 100건 이상, 별점 4.3 이상이면 우선.
- **제외**: 대여·렌탈, 중고·리퍼, 갤럭시/아이폰 전용처럼 기종이 좁은 것, 배송이 2주 이상인 해외직구,
  이미 발사대에 올린 상품(같은 상품 재사용 금지), 영상과 상품이 명백히 다른 것.
- **기록**: 후보는 `drafts/candidates/{YYYY-MM-DD}.md` 에 `상품 / 가격 / 링크 / 리뷰수 / 영상 유무 / 한 줄 이유` 표로.
  탈락도 이유를 한 줄 남긴다(같은 후보를 다음 주기에 다시 조사하지 않게).
- **숫자는 API 응답에 있는 값만.** 가격은 조회 시각을 함께 적는다.

---

## 4. 클립 준비 규격

| 항목 | 값 |
|---|---|
| 해상도 | **720x1280** (9:16, 크롭·스케일, `setsar=1`) |
| 파일명 | `{id}_clip_small.mp4` (`output/{id}/clips/`) |
| 길이 | 20초 기본 (`--duration`으로 조정) |
| 프레임 검사 | **0초 / 0.5초 / 1.5초** JPG — 첫 장면이 검은 화면·자막 겹침인지 확인 |
| 오디오 | 원본 유지(AAC 128k). 무음 원본이면 무음 그대로 |
| 커밋 | 영상 파일은 **절대 커밋 금지**. 프레임·`clip_manifest.json`만 필요하면 `drafts/{id}/clips/`로 복사 |

- 워터마크·타 계정 로고가 박힌 소스는 쓰지 않는다. 출처 크리에이터와 권리 표시는 글감 `rights`에 적는다.
- 남의 영상을 그대로 재업로드하는 전제의 문구를 쓰지 않는다. 직접 촬영 또는 허락+크레딧.

---

## 5. 글감 패키지 계약 (`feed/{날짜}/{id}.json`)

- 파일명 13자 = `id` (`YYYYMMDD_HHMM`). 필수 필드는 `feed/SCHEMA.md` 참고 — 하나도 빼지 않는다.
- `hooks`: `threads`(호들갑 반말) · `x`(280자 이내) · `instagram_reels`(해시태그 포함 초안).
- `first_comment`: 상품명·가격·링크·주의·대가성 문구.
- `disclosure`: 원문 그대로. `caveats`: 영상-상품 일치 여부, 가격 기준일, 권리, 결제 여부.
- `published_urls`: 아직 안 올렸으면 `null`. **게시는 유리/사람이 한다.**
- `video.fetch_command`: 맥미니에서 같은 구간을 다시 받을 수 있는 명령(yt-dlp + ffmpeg).
- 쓰고 나면 `python3 feed/tools/build_index.py` → `feed/index.json`·`latest.json` 갱신.

---

## 6. 산출물 파일명 (고정)

`output/{id}/` 과 `drafts/{id}/` 에서 이름을 바꾸면 스크립트·사람이 못 찾는다.

| 파일 | 내용 |
|---|---|
| `shorts_script.md` | 훅 3안(각 2줄) + 20초 타임라인(0~3s/3~8s/8~15s/15~20s) + 직접 촬영 컷 3개 이상 |
| `captions.srt` | 큐 5개 이하, 한 줄 20자 안팎, `00:00:00,000 --> 00:00:05,000` 형식 |
| `blog_draft.md` | H1 + H2 3~4개, 900~1,300자, 정보형 존댓말, 스펙 표 포함 |
| `hotdeal_card.md` | 카드뉴스 5장 문구(표지/문제/제품/스펙·주의/링크·대가성), 장당 2줄 이내 |
| `codex_output.md` | 무엇을 만들었는지 5줄 요약 + 사람이 확인할 항목 |
| `clip_manifest.json` | 클립 규격·프레임·검사 결과 (`clip_prep.py`가 생성) |

---

## 7. 문체 규칙

- **숏폼(스레드·X·릴스)**: 짧은 입말 반말, 스크롤을 멈추는 호들갑 톤(`ㅋㅋ`, `😭`, `!!!!`). 한 줄 30자 안팎, 줄바꿈으로 끊는다.
  이모지는 줄당 1개 이하. 평이한 설명조·광고 문구는 쓰지 않는다.
- **블로그**: 정보형 존댓말(`~습니다`), 표는 스펙 3~6행, 과장 최상급 금지. 도입(문제)→사용 관점→스펙 표→주의→링크→대가성.
- **카드뉴스**: 한 장에 한 메시지. 숫자는 문장으로.
- 금지 표현(AI 말투): "결론적으로", "다양한 측면에서", "~할 수 있습니다만", "도움이 되셨길 바랍니다",
  "살펴보았습니다"로 시작하는 상투적 도입, 같은 문장 구조 반복.
- 경원 님이 싫어하는 것: 판매자 광고 같은 문장, 근거 없는 칭찬, 정렬 안 맞는 표. 표는 열을 맞춘다.

---

## 8. 사실 규칙 (가장 중요)

- **글감 JSON·API 응답에 있는 값만 사실로 쓴다.** 가격·할인율·리뷰 수·평점·배터리 시간·소재를 새로 만들지 않는다.
- 없는 값은 `확인 필요`, 추정이면 `추정`이라고 명시한다.
- `disclosure`(대가성 문구)는 원문 그대로. `caveats`는 하나도 빠뜨리지 않는다.
- 영상 속 제품과 링크 상품이 **같은 모델이라고 단정하지 않는다**. 글감에 적힌 수준(형태 대조/동일 방식)만 쓴다.
- "조회수 2,250만" 같은 수치는 `rights.metrics`에 있는 것만, 확인 시점과 함께.

---

## 9. 보고 규칙

- `python3 scripts/report_cycle.py` 가 `reports/latest.md`(현재 상태)·`reports/daily/{날짜}.md`(한 줄 요약)를 자동 생성한다.
  손으로 덮어쓰지 않는다. 빠진 항목이 있으면 스크립트에 넣을 제안을 `improvements.md`에 쓴다.
- **개선 제안은 `reports/improvements.md`** 에 날짜별 한 줄로:
  ```
  ## 2026-11-23
  - 뽐뿌 RSS가 15시 이후 403 빈발 → feeds.json에서 15~18시 건너뛰기 제안 (근거: 로그 3회)
  - shorts_script 훅 3안이 서로 비슷해짐 → 훅 유형(가격/불편/반전)을 나눠 달라는 규칙 제안
  ```
- 유리는 이 파일과 `latest.md`만 읽고 `AGENTS.md`·`SCHEMA.md`를 고친다. 맥미니는 규칙 파일을 고치지 않는다.
- 보고에 쓰는 경로는 `~`로 줄인다(공개 저장소). 키 값은 절대 쓰지 않고 "설정됨/없음"만.

---

## 10. 금지 행동

- ❌ SNS·블로그에 **직접 게시**하지 않는다. 초안·패키지까지. 게시는 유리/사람.
- ❌ 결제·구매·장바구니·회원가입·로그인 대행을 하지 않는다.
- ❌ 남의 영상을 다운로드해 다시 올리는 것을 전제로 한 문구를 쓰지 않는다.
- ❌ `AGENTS.md`·`feed/SCHEMA.md`·`scripts/`·`setup_macmini.sh`를 고치지 않는다.
- ❌ `reports/`·`drafts/`·`feed/` 밖의 변경을 커밋하지 않는다. `git push`는 그 세 곳만.
- ❌ `.env`, 키, 쿠키, 세션, 개인정보(전화번호·주소)를 파일·로그·커밋에 남기지 않는다.
- ❌ `adjurer`의 다른 저장소를 읽거나 쓰지 않는다.

---

## 11. 실패·재시도

- 피드가 비었으면 빈 파일을 만들지 말고 "새 글감 없음"만 보고한다.
- 한 글감이 실패해도 다음 글감은 계속한다. 실패 id는 `state`에 넣지 않아 다음 주기에 재시도된다(codex 호출은 3회까지).
- codex CLI·키가 없으면 초안 템플릿만 남기고 보고에 "왜 자동 생성이 멈췄는지"를 적는다.
- 클립 인코딩 실패는 원본을 지우지 말고 `output/{id}/clips/`에 실패 로그를 남긴다.
- push 실패는 1회 `pull --rebase` 후 재시도. 그래도 실패하면 커밋만 남기고 보고에 적는다(다음 주기에 다시 올라간다).
- 로그: `logs/codex-sync.log` · `logs/hotdeal-radar.log` · `logs/codex-report.log`.

---

## 12. 자주 쓰는 명령

```bash
cd ~/factory/macmini-codex-bot

python3 scripts/codex_sync.py --dry-run              # 새 글감 초안만(상태·drafts 안 씀)
python3 scripts/codex_sync.py --run-codex            # 실제 처리 + drafts 커밋·푸시
python3 scripts/coupang_api.py goldbox --limit 20    # 골드박스 특가
python3 scripts/coupang_api.py search "미니 가습기" --limit 10
python3 scripts/clip_prep.py --check                 # ffmpeg/yt-dlp 확인
python3 scripts/clip_prep.py --src <URL> --id <id>   # 720x1280 + 프레임 0/0.5/1.5
python3 scripts/hotdeal_radar.py --dry-run           # 핫딜 미리보기
python3 scripts/report_cycle.py                      # 보고 → reports/ 커밋·푸시
tail -f logs/codex-sync.log logs/codex-report.log
```

- 설치·launchd: `README.md` / `bash setup_macmini.sh`
- 글감 스키마: `../feed/SCHEMA.md`
- 쿠팡 API 서명: `scripts/coupang_api.py` (키 없으면 자동 스킵)
