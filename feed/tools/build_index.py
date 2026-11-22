#!/usr/bin/env python3
"""feed/index.json · feed/latest.json 재생성 + 글감 JSON 스키마 검증.

사용법:
    python3 feed/tools/build_index.py            # 검증 후 index.json·latest.json 갱신
    python3 feed/tools/build_index.py --check    # 검증만 (파일 쓰지 않음, CI/훅용)
    python3 feed/tools/build_index.py --print    # 갱신 결과를 표준출력으로만

표준 라이브러리만 사용합니다. python3.9+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

FEED_DIR = Path(__file__).resolve().parent.parent          # …/feed
KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1

ID_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})$")

REQUIRED = ["id", "created_at", "product_name", "price_krw", "discount_rate",
            "coupang_short_url", "video_source_url", "hooks", "first_comment",
            "disclosure", "caveats", "published_urls"]
REQUIRED_HOOKS = ["threads", "x", "instagram_reels"]
REQUIRED_PUBLISHED = ["threads", "x"]


def load(path: Path) -> dict:
    """글감 JSON을 읽는다.

    과거 회차가 latest.json 형식({"item": {...}})으로 저장한 파일도
    데이터를 잃지 않고 그대로 읽도록 래핑을 풀어 준다.
    """
    with path.open(encoding="utf-8") as fp:
        data = json.load(fp)
    if isinstance(data, dict) and "item" not in data and "created_at" not in data and "id" not in data:
        raise ValueError("알 수 없는 형식: id/created_at도 item 래핑도 없음")
    if isinstance(data, dict) and isinstance(data.get("item"), dict) and "created_at" not in data:
        data = dict(data["item"])
    return data


def validate(item: dict, path: Path) -> list[str]:
    """한 글감의 필수 필드·타입을 확인하고 문제 목록을 돌려준다."""
    errs: list[str] = []
    rel = path.relative_to(FEED_DIR.parent).as_posix()

    for key in REQUIRED:
        if key not in item:
            errs.append(f"{rel}: 필수 필드 누락 '{key}'")

    item_id = item.get("id", "")
    m = ID_RE.match(str(item_id))
    if not m:
        errs.append(f"{rel}: id 형식이 YYYYMMDD_HHMM 이 아님 ({item_id!r})")
    elif item_id != path.stem:
        errs.append(f"{rel}: id({item_id})와 파일명({path.stem})이 다름")
    elif path.parent.name != f"{m.group(1)}-{m.group(2)}-{m.group(3)}":
        errs.append(f"{rel}: 날짜 폴더가 id와 다름 (기대 {m.group(1)}-{m.group(2)}-{m.group(3)})")

    for key in ("price_krw", "discount_rate"):
        val = item.get(key)
        if not isinstance(val, int) or isinstance(val, bool):
            errs.append(f"{rel}: '{key}'가 정수가 아님 ({val!r})")

    hooks = item.get("hooks") or {}
    for key in REQUIRED_HOOKS:
        if not isinstance(hooks.get(key), str) or not hooks.get(key, "").strip():
            errs.append(f"{rel}: hooks.{key} 문자열이 비었음")

    pub = item.get("published_urls") or {}
    for key in REQUIRED_PUBLISHED:
        if key not in pub:
            errs.append(f"{rel}: published_urls.{key} 키 없음 (미게시면 null)")

    caveats = item.get("caveats")
    if not isinstance(caveats, list) or not all(isinstance(c, str) for c in caveats):
        errs.append(f"{rel}: caveats는 문자열 배열이어야 함")

    ali = item.get("ali_short_url")
    if ali is not None and not str(ali).startswith("http"):
        errs.append(f"{rel}: ali_short_url 형식 오류 ({ali!r})")

    cow = item.get("coupang_short_url") or ""
    if not str(cow).startswith("https://"):
        errs.append(f"{rel}: coupang_short_url은 https 링크여야 함")

    return errs


def collect() -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errs: list[str] = []
    for path in sorted(FEED_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/*.json")):
        try:
            item = load(path)
        except json.JSONDecodeError as exc:
            errs.append(f"{path.relative_to(FEED_DIR.parent)}: JSON 파싱 실패 — {exc}")
            continue
        errs.extend(validate(item, path))
        items.append({**item, "_path": path})
    return items, errs


def index_entry(item: dict) -> dict:
    pub = item.get("published_urls") or {}
    return {
        "id": item.get("id"),
        "created_at": item.get("created_at"),
        "date": item.get("_path").parent.name,
        "set_no": item.get("set_no"),
        "product_name": item.get("product_name"),
        "price_krw": item.get("price_krw"),
        "discount_rate": item.get("discount_rate"),
        "path": item.get("_path").relative_to(FEED_DIR.parent).as_posix(),
        "coupang_short_url": item.get("coupang_short_url"),
        "published": bool(pub.get("threads") or pub.get("x")),
        "published_urls": {"threads": pub.get("threads"), "x": pub.get("x")},
        "tags": item.get("tags", []),
    }


def build(items: list[dict], now: datetime) -> tuple[dict, dict | None]:
    items = sorted(items, key=lambda it: str(it.get("created_at", "")), reverse=True)
    entries = [index_entry(it) for it in items]
    index = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now.isoformat(timespec="seconds"),
        "count": len(entries),
        "dates": sorted({e["date"] for e in entries}, reverse=True),
        "items": entries,
    }
    latest = None
    if items:
        newest = items[0]
        body = {k: v for k, v in newest.items() if not k.startswith("_")}
        latest = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now.isoformat(timespec="seconds"),
            "id": body.get("id"),
            "path": index["items"][0]["path"],
            "item": body,
        }
    return index, latest


def dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="검증만 하고 파일을 쓰지 않는다")
    ap.add_argument("--print", dest="to_stdout", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    items, errs = collect()
    if errs:
        print("글감 검증 실패:", file=sys.stderr)
        for e in errs:
            print("  -", e, file=sys.stderr)
        return 1
    if not items:
        print(f"글감이 없습니다: {FEED_DIR}", file=sys.stderr)
        return 1

    index, latest = build(items, datetime.now(KST))

    if args.check:
        print(f"검증 OK — 글감 {len(items)}건 (index·latest 갱신은 하지 않음)")
        return 0

    if args.to_stdout:
        print(dump(index))
        if latest:
            print(dump(latest))
        return 0

    (FEED_DIR / "index.json").write_text(dump(index), encoding="utf-8")
    if latest:
        (FEED_DIR / "latest.json").write_text(dump(latest), encoding="utf-8")
    print(f"갱신 완료 — 글감 {len(items)}건, 최신 {index['items'][0]['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
