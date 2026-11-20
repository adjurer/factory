# factory

경원 님(@adjurer)의 콘텐츠 자동화 작업 저장소입니다. 두 부분으로 나뉩니다.

| 폴더 | 무엇 | 누가 씀 |
|---|---|---|
| [`feed/`](feed/SCHEMA.md) | 오늘 만든 숏폼·글감 JSON 피드(쿠팡 파트너스 세트) | 유리(메인 에이전트)가 push |
| [`macmini-codex-bot/`](macmini-codex-bot/README.md) | 맥미니에서 상시 도는 Codex 봇(핫딜 레이더 + 글감 → 초안 생성) | 맥미니가 pull |

## 흐름

```
유리 ──(feed/YYYY-MM-DD/*.json + index.json push)──▶ GitHub(adjurer/factory)
                                                        │  git pull (30분)
                                                        ▼
                                        맥미니: codex_sync.py → output/{id}/
                                        맥미니: hotdeal_radar.py → 텔레그램/Discord/X
```

- 글감 스키마: [`feed/SCHEMA.md`](feed/SCHEMA.md)
- 봇 설치·실행: [`macmini-codex-bot/README.md`](macmini-codex-bot/README.md)
- Codex 작업 규칙: [`macmini-codex-bot/AGENTS.md`](macmini-codex-bot/AGENTS.md)

## 원칙

- **PUBLIC 저장소입니다.** API 키·비밀번호·토큰·집주소·전화번호를 커밋하지 않습니다(`.env.example`에 키 이름만).
- 봇은 게시·결제·구매를 하지 않습니다. 초안과 알림까지입니다.
- 영상은 남의 크리에이터 것입니다. 재업로드 대신 직접 촬영 또는 허락+크레딧.
- `adjurer` 계정의 다른 저장소(`adjurer.github.io`, `hermes-agent`, `2026win`, `cardnews-creator`,
  `live-speech-hub`)는 이 봇과 무관하며 건드리지 않습니다.
