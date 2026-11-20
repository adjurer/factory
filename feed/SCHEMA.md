# feed/ 스키마 — 글감 피드 v1

`adjurer/factory` 저장소에서 **유리(메인 에이전트) → 맥미니**로 글감을 넘기는 통로입니다.
유리가 새 숏폼 세트를 만들면 이 폴더에 JSON 1개를 추가하고 `push`하고, 맥미니의 Codex 봇은 `git pull` 후
`feed/index.json`(전체 목록)과 `feed/latest.json`(최신 1건)만 읽으면 새 글감을 알 수 있습니다.

## 폴더 구조

```
feed/
├── SCHEMA.md              이 문서
├── index.json             전체 목록(최신순) — 봇의 진입점 ①
├── latest.json            가장 최근 1건의 전체 내용 — 봇의 진입점 ②
├── 2026-11-20/            날짜 폴더
│   ├── 20261120_0830.json
│   ├── 20261120_1100.json
│   └── …
└── tools/
    └── build_index.py     index.json·latest.json 재생성 + 스키마 검증 (표준 라이브러리만)
```

## 새 글감을 넣는 절차 (유리 쪽)

1. `feed/{YYYY-MM-DD}/{YYYYMMDD_HHMM}.json` 생성 — 파일명 13자가 곧 `id`입니다.
2. `python3 feed/tools/build_index.py` 실행 → `index.json`, `latest.json` 자동 갱신 + 필수 필드 검증
   (검증 실패 시 exit 1, 아무 파일도 쓰지 않음).
3. `git add -A && git commit && git push` → 맥미니 봇은 다음 주기의 `git pull`에서 자동으로 집어갑니다.

한 번 올린 JSON은 고치지 않는 것이 원칙입니다. 가격·게시 URL처럼 나중에 바뀌는 값은 `revisions` 배열에
덧붙이세요. 봇은 `id` 기준으로 중복을 거르기 때문에, 내용만 고치면 이미 처리된 글감은 다시 만들지 않습니다.

## item 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `schema_version` | int | ✔ | 현재 `1` |
| `id` | string | ✔ | `YYYYMMDD_HHMM` — 파일명과 반드시 동일 |
| `set_no` | int | | 발사대 회차(1호, 2호 …) |
| `created_at` | string | ✔ | 세트 제작 시각, ISO 8601 `+09:00` |
| `product_name` | string | ✔ | 쿠팡 상품명(옵션명 포함) |
| `price_krw` | int | ✔ | 게시 기준 판매가(원) |
| `list_price_krw` | int | | 정가(원) |
| `discount_rate` | int | ✔ | 할인율(%) |
| `coupang_short_url` | string | ✔ | 쿠팡 파트너스 단축 링크 |
| `ali_short_url` | string | | 알리 어필리에이트 단축 링크 — **실제로 게시에 쓴 경우에만**. 없거나 안 썼으면 키를 넣지 않거나 `null` |
| `video_source_url` | string | ✔ | 원본 영상 URL(TikTok 등) |
| `hooks` | object | ✔ | `threads`, `x`, `instagram_reels` — 각 string(본문 전문) |
| `first_comment` | string | ✔ | 첫 댓글 전문. 상품명·가격·링크·주의사항·대가성 문구 포함 |
| `disclosure` | string | ✔ | 대가성(제휴) 문구 — 게시물에 반드시 노출 |
| `caveats` | array\<string\> | ✔ | 올리기 전 알아둘 점: 권리, 표기 가격 기준, 영상-상품 불일치, 결제 여부 |
| `published_urls` | object | ✔ | `threads`, `x` (미게시면 `null`). 부가 키 `x_reply_a`, `x_reply_b` |
| `product` | object | | `brand`, `model`, `coupang_product_id`, `coupang_item_id`, `coupang_vendor_item_id`, `rating`, `review_count`, `shipping` |
| `ali` | object\|null | | 알리 대체·비교 상품: `product_name`, `price_krw`, `url`, `item_id`, `note` |
| `ali_note` | string | | 알리 링크를 못 만들었거나 안 쓴 이유 |
| `video` | object | | `workspace_path`, `duration_sec`, `resolution`, `source_trim`, `fetch_command` |
| `rights` | object | | `creator`, `creator_url`, `metrics`, `reuse_note` |
| `tags` | array\<string\> | | 필터·중복 방지용 태그(예: `보풀제거기`, `살림`) |
| `revisions` | array\<object\> | | 사후 변경 기록 `{at, field, from, to, reason}` |

### hooks에 대해

- `threads` / `x`: 실제 게시한 문구 그대로. `x`는 280자 제한 때문에 짧게 유지합니다.
- `instagram_reels`: 아직 릴스 자동 게시는 하지 않습니다. 봇이 릴스 캡션·자막을 만들 때 쓰는 **초안 문구**입니다
  (해시태그 포함). 그대로 신뢰할 게시 기록이 아니라 `draft`로 다루세요.

### video.fetch_command에 대해

`video.workspace_path`는 유리 작업공간 기준 경로라 맥미니에는 없습니다. 맥미니에서 영상이 필요하면
`video.fetch_command`(yt-dlp + ffmpeg)로 원본에서 같은 구간을 다시 받으세요. 영상 파일 자체는
공개 저장소 용량 문제로 커밋하지 않습니다.

## index.json

```json
{
  "schema_version": 1,
  "updated_at": "2026-11-20T17:42:31+09:00",
  "count": 4,
  "dates": ["2026-11-20"],
  "items": [
    {
      "id": "20261120_1700",
      "created_at": "2026-11-20T17:00:00+09:00",
      "date": "2026-11-20",
      "set_no": 4,
      "product_name": "…",
      "price_krw": 40160,
      "discount_rate": 32,
      "path": "feed/2026-11-20/20261120_1700.json",
      "coupang_short_url": "…",
      "published": true,
      "published_urls": {"threads": "…", "x": "…"},
      "tags": ["…"]
    }
  ]
}
```

- `items`는 `created_at` **내림차순**(최신 먼저).
- `path`는 저장소 루트 기준 상대경로 — 봇은 `repo_root / path`로 엽니다.

## latest.json

```json
{
  "schema_version": 1,
  "updated_at": "…",
  "id": "20261120_1700",
  "path": "feed/2026-11-20/20261120_1700.json",
  "item": { …그 JSON 전체… }
}
```

## 봇 쪽 계약

- 봇은 `index.json`의 `items[].id`를 `state/processed_ids.json`과 비교해 새 것만 처리합니다.
- 필드가 일부 비어도 동작해야 합니다: `id`, `product_name`, `first_comment`가 있으면 나머지는 기본값으로 채웁니다.
- `published_urls`가 채워진 글감은 "이미 올라간 것"이므로 봇은 **추가 발행용 문구**만 만들고 다시 올리지 않습니다.
- 가격·스펙은 JSON에 적힌 값을 그대로 씁니다. 봇이 새 수치를 추정해 쓰면 안 됩니다.
