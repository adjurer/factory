# factory

> **⚖️ 헌법 필독: 이 저장소에서 일하는 모든 AI는 작업 전에 [`CONSTITUTION.md`](CONSTITUTION.md)를 먼저 읽는다.**
> 역할 분담·채널 지도·서식·보안 금지선·재귀개선 루프가 거기에 있다. 이 README·`AGENTS.md`와 충돌하면 헌법이 우선한다.

경원 님(@adjurer)의 콘텐츠 자동화 작업 저장소입니다. 두 부분으로 나뉩니다.

| 폴더 | 무엇 | 누가 씀 |
|---|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | **저장소 헌법** — 정체성·역할·채널·서식·보안·개선 루프 | 유리(개정), 모든 AI(필독) |
| [`feed/`](feed/SCHEMA.md) | 발사대 숏폼 글감 JSON 피드 | 유리도 쓰고, **맥미니가 완성 패키지를 적재**하기도 함 |
| [`macmini-codex-bot/`](macmini-codex-bot/README.md) | 맥미니에서 상시 도는 Codex 봇(소싱·클립·초안·보고) | 맥미니 |

## 역할 분담

경원 님 지시(2026-11-23): **유리 사용량은 최소화, 맥미니(맥미니 + Codex 구독)가 무거운 작업을 반자동 처리.**

| 맥미니 | 유리 |
|---|---|
| 핫딜·쿠팡 API 소싱, 클립 720x1280 준비, 카피·구매판단표·블로그 초안, `reports/` 푸시 | 하루 1~2회 보고·초안만 읽고 판단, 규칙 개선, 원격 게시·최종 검수 |

## 흐름

```
                 ┌─ feed/ (유리 글감 + 맥미니 완성 패키지) ─┐
                 │                                          │
유리 ──push──────┘                                          ▼
                                          맥미니: git pull --rebase (30분)
                                          ├ codex_sync → output/{id}/ 초안
                                          ├ hotdeal_radar → 알림 (15분)
                                          ├ clip_prep → 720x1280 클립·프레임
                                          └ report_cycle → reports/latest.md
                                                   │ drafts/ · reports/ commit·push
                                                   ▼
                                          유리: reports/·drafts/ 읽기
                                          → 규칙(AGENTS.md·SCHEMA.md) 개선
                                          → 게시·검수·경원 님 결정
```

- [`CONSTITUTION.md`](CONSTITUTION.md) — **헌법(최우선)**
- 글감 스키마: [`feed/SCHEMA.md`](feed/SCHEMA.md)
- 봇 설치·실행: [`macmini-codex-bot/README.md`](macmini-codex-bot/README.md)
- Codex 작업 규칙(역할 분담·쓰기 경계): [`macmini-codex-bot/AGENTS.md`](macmini-codex-bot/AGENTS.md)
- 맥미니 보고: [`macmini-codex-bot/reports/`](macmini-codex-bot/reports/README.md)

## 원칙

- **PUBLIC 저장소입니다.** API 키·비밀번호·토큰·집주소·전화번호를 커밋하지 않습니다(`.env.example`에 키 이름만).
- 봇은 게시·결제·구매를 하지 않습니다. 초안·패키지·알림까지입니다.
- 영상은 남의 크리에이터 것입니다. 재업로드 대신 직접 촬영 또는 허락+크레딧. 영상 파일은 커밋하지 않습니다.
- `adjurer` 계정의 다른 저장소(`adjurer.github.io`, `hermes-agent`, `2026win`, `cardnews-creator`,
  `live-speech-hub` 등)는 이 봇과 무관하며 건드리지 않습니다.
