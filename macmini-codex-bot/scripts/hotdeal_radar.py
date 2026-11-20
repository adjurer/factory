#!/usr/bin/env python3
"""핫딜 레이더 — 공개 핫딜 피드(RSS)를 주기적으로 훑어 IT·가성비 키워드만 골라 알린다.

동작 순서
  1) config/feeds.json(또는 내장 기본값)의 RSS 목록을 받아온다
  2) 최근 N분 안에 올라온 글만 남긴다
  3) 포함 키워드(IT·가성비)에 걸리고 제외 키워드에 안 걸리는 것만 남긴다
  4) state/seen.json 으로 이미 본 글(제목+링크 해시)을 걸러낸다
  5) 쿠팡 키가 있으면 글 안의 쿠팡 링크를 파트너스 단축 링크로 바꾼다
  6) 텔레그램 / Discord / X(릴레이 웹훅) 중 설정된 채널로 보낸다

사용법
    python3 scripts/hotdeal_radar.py --dry-run          # 아무것도 보내지 않고 미리보기만
    python3 scripts/hotdeal_radar.py                    # 실제 알림(채널이 설정된 경우)
    python3 scripts/hotdeal_radar.py --dry-run --json   # 기계가 읽을 JSON으로
    python3 scripts/hotdeal_radar.py --feed https://... # RSS 하나 추가로 확인

`--dry-run`은 state/seen.json 을 쓰지 않고, 어떤 채널로도 전송하지 않습니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import BOT_DIR, http_get, http_post_json, read_json, write_json  # noqa: E402

KST = timezone(timedelta(hours=9))
CONFIG_PATH = BOT_DIR / "config" / "feeds.json"
STATE_PATH = BOT_DIR / "state" / "seen.json"

# 2026-11-20 저장소 점검 시점에 실제로 응답한 피드만 기본 활성화했습니다.
# 막힌 곳(403 CHALLENGE_REQUIRED / 404 / 타임아웃)은 enabled=false 로 두고 사유를 적었습니다.
# 맥미니 네트워크에서 `curl -sS -A "<브라우저 UA>" <URL> | head -3` 로 확인한 뒤 true 로 바꾸세요.
DEFAULT_CONFIG = {
    "feeds": [
        {"name": "뽐뿌게시판", "url": "https://www.ppomppu.co.kr/rss.php?id=ppomppu", "enabled": True},
        {"name": "알구몬", "url": "https://www.algumon.com/rss", "enabled": False,
         "note": "2026-11-20 확인: 403 CHALLENGE_REQUIRED(봇 차단). 브라우저에서 열리면 true로."},
        {"name": "클리앙 알뜰구매", "url": "https://www.clien.net/service/rss?board=jippum", "enabled": False,
         "note": "2026-11-20 확인: 404. 클리앙이 RSS를 닫았거나 차단 중."},
        {"name": "루리웹", "url": "https://bbs.ruliweb.com/rss/deal", "enabled": False,
         "note": "2026-11-20 확인: 연결 타임아웃."},
    ],
    "include_keywords": [
        "ssd", "nvme", "hdd", "ddr4", "ddr5", "메모리", "cpu", "gpu", "그래픽카드", "메인보드", "파워서플라이",
        "모니터", "키보드", "마우스", "이어폰", "헤드폰", "헤드셋", "스피커", "사운드바", "웹캠", "마이크",
        "충전기", "케이블", "c타입", "usb", "보조배터리", "공유기", "와이파이", "nas", "미니pc",
        "노트북", "태블릿", "아이패드", "스마트워치", "모니터암", "모션데스크", "키캡", "쿨러", "케이스",
        "공기청정", "가습", "제습", "선풍기", "청소기", "로봇청소", "전동", "무선", "프린터", "스캐너",
        "에어프라이어", "블렌더", "정수기", "건조기", "충전", "역대급", "핫딜", "특가", "최저가", "1+1", "반값",
    ],
    "exclude_keywords": ["중고", "리퍼", "삽니다", "구합니다", "매입", "판매완료", "품절"],
    "max_age_minutes": 180,
    "max_notify_per_run": 5,
    "seen_retention_days": 30,
}

PRICE_RE = re.compile(r"([\d,]{3,})\s*원")
TAG_RE = re.compile(r"^\[([^\]]{1,20})\]")


# --------------------------------------------------------------------------- 피드


def load_config(path: "Path" = CONFIG_PATH) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    user = read_json(path)
    if isinstance(user, dict):
        for key, value in user.items():
            cfg[key] = value
    return cfg


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str) -> "list[dict]":
    """RSS 2.0 / Atom을 같은 모양으로 뽑는다."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise RuntimeError(f"XML 파싱 실패 — {exc}")

    out: "list[dict]" = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        row = {"title": "", "link": "", "description": "", "published": None}
        for child in node:
            name = _localname(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                row["title"] = re.sub(r"\s+", " ", text)
            elif name == "link":
                if text:
                    row["link"] = text
                elif child.get("href"):
                    row["link"] = child.get("href", "")
            elif name in ("description", "summary", "content"):
                if not row["description"]:
                    row["description"] = re.sub(r"<[^>]+>", " ", text)[:300]
            elif name in ("pubdate", "published", "updated", "date"):
                row["published"] = text or row["published"]
        if row["title"]:
            out.append(row)
    return out


def parse_when(value: "str | None") -> "datetime | None":
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def parse_price(title: str) -> "int | None":
    m = PRICE_RE.search(title)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------- 필터


def keyword_hits(title: str, keywords: "list[str]") -> "list[str]":
    low = title.lower()
    return [kw for kw in keywords if kw.lower() in low]


def item_key(item: dict) -> str:
    """제목 정규화 + 링크(쿼리 제외) 해시 — 같은 글이 다시 올라와도 한 번만 알린다."""
    base = item.get("link", "").split("?", 1)[0]
    norm = re.sub(r"[\s\[\](){}·…\-–—]+", "", item.get("title", "").lower())
    return hashlib.sha1(f"{norm}|{base}".encode("utf-8")).hexdigest()[:16]


def collect(cfg: dict, log, feed_overrides: "list[str] | None" = None) -> "list[dict]":
    feeds = [f for f in cfg.get("feeds", []) if f.get("enabled")]
    for url in feed_overrides or []:
        feeds.append({"name": f"직접 지정 {url}", "url": url, "enabled": True})
    if not feeds:
        log.warning("활성화된 피드가 없습니다 — config/feeds.json 의 enabled 를 확인하세요")
        return []

    rows: "list[dict]" = []
    for feed in feeds:
        try:
            xml_text = http_get(feed["url"], timeout=20, retries=1)
            parsed = parse_feed(xml_text)
            log.info("피드 수신 %s — %d건", feed["name"], len(parsed))
        except Exception as exc:  # 피드 하나가 죽어도 나머지는 계속
            log.warning("피드 실패 %s — %s", feed["name"], exc)
            continue
        for row in parsed:
            row["source"] = feed["name"]
            row["price_krw"] = parse_price(row["title"])
            tag = TAG_RE.match(row["title"])
            row["shop"] = tag.group(1) if tag else None
            rows.append(row)
    return rows


def select(rows: "list[dict]", cfg: dict, seen: dict, now: datetime, log) -> "list[dict]":
    max_age = int(cfg.get("max_age_minutes", 180))
    cutoff = now - timedelta(minutes=max_age)
    include = cfg.get("include_keywords", [])
    exclude = cfg.get("exclude_keywords", [])

    picked: "list[dict]" = []
    stats = {"too_old": 0, "no_keyword": 0, "excluded": 0, "already_seen": 0, "dup_in_run": 0}
    run_keys = set()

    for row in rows:
        when = parse_when(row.get("published"))
        if when and when < cutoff:
            stats["too_old"] += 1
            continue
        hits = keyword_hits(row["title"], include)
        if not hits:
            stats["no_keyword"] += 1
            continue
        if keyword_hits(row["title"], exclude):
            stats["excluded"] += 1
            continue
        key = item_key(row)
        if key in seen or key in run_keys:
            stats["already_seen" if key in seen else "dup_in_run"] += 1
            continue
        run_keys.add(key)
        row["key"] = key
        row["matched"] = hits
        picked.append(row)

    picked.sort(key=lambda r: (parse_when(r.get("published")) or datetime.min.replace(tzinfo=KST)),
                reverse=True)
    log.info("필터 결과 — 후보 %d건 (%s)", len(picked), ", ".join(f"{k} {v}" for k, v in stats.items()))
    return picked


# --------------------------------------------------------------------------- 메시지


def format_message(item: dict, link: str, index: int, total: int) -> str:
    head = f"🔥 핫딜 레이더 ({index}/{total}) · {item.get('source', '')}"
    lines = [head, item["title"]]
    if item.get("price_krw"):
        lines.append(f"가격: {item['price_krw']:,}원")
    lines.append(link)
    lines.append(f"· 키워드: {', '.join(item.get('matched', [])[:4])}")
    return "\n".join(lines)


def configured_channels() -> "list[str]":
    """.env에 실제로 채워진 알림 채널 이름 목록."""
    found: "list[str]" = []
    if (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() and (os.environ.get("TELEGRAM_CHAT_ID") or "").strip():
        found.append("telegram")
    if (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip():
        found.append("discord")
    if (os.environ.get("X_WEBHOOK_URL") or "").strip():
        found.append("x-relay")
    return found


def notify(text: str, dry_run: bool, log) -> "list[dict]":
    """설정된 채널로 보낸다. 반환: [{channel, ok, detail}]."""
    if dry_run:
        return [{"channel": "dry-run", "ok": True, "detail": "전송하지 않음"}]

    results: "list[dict]" = []

    tg_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    tg_chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if tg_token and tg_chat:
        url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
        status, body = http_post_json(url, {"chat_id": tg_chat, "text": text,
                                            "disable_web_page_preview": False})
        results.append({"channel": "telegram", "ok": status == 200, "detail": f"HTTP {status} {body[:120]}"})

    discord = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if discord:
        status, body = http_post_json(discord, {"content": text[:1900]})
        results.append({"channel": "discord", "ok": 200 <= status < 300, "detail": f"HTTP {status}"})

    x_hook = (os.environ.get("X_WEBHOOK_URL") or "").strip()
    if x_hook:
        # X는 웹훅을 기본 제공하지 않습니다. Zapier/Make/n8n 같은 릴레이에 넘기는 용도.
        status, body = http_post_json(x_hook, {"text": text[:280]})
        results.append({"channel": "x-relay", "ok": 200 <= status < 300, "detail": f"HTTP {status}"})

    if not results:
        log.warning("알림 채널이 없습니다(.env의 TELEGRAM_*, DISCORD_WEBHOOK_URL, X_WEBHOOK_URL) — 미리보기만 했습니다")
        return [{"channel": "none", "ok": False, "detail": "채널 미설정"}]

    for r in results:
        log.info("전송 %s — %s (%s)", r["channel"], "성공" if r["ok"] else "실패", r["detail"])
    return results


def enrich_link(item: dict, use_affiliate: bool, log) -> str:
    link = item.get("link", "")
    if not use_affiliate or "coupang.com" not in link:
        return link
    try:
        import coupang_api  # 같은 scripts/ 폴더
    except Exception:
        return link
    if not coupang_api.has_keys():
        log.debug("쿠팡 키 없음 — 원본 링크 사용")
        return link
    short = coupang_api.first_shorten_url(link)
    return short or link


def prune_seen(seen: dict, retention_days: int, now: datetime) -> dict:
    cutoff = now - timedelta(days=retention_days)
    kept = {}
    for key, meta in seen.items():
        stamp = meta.get("first_seen") if isinstance(meta, dict) else None
        try:
            dt = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            dt = now
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        if dt >= cutoff:
            kept[key] = meta
    return kept


# --------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공개 핫딜 피드 → IT·가성비 필터 → 알림")
    ap.add_argument("--dry-run", action="store_true", help="전송·상태저장 없이 미리보기만")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 알릴 최대 건수")
    ap.add_argument("--feed", action="append", default=[], help="RSS URL 추가(여러 번 가능)")
    ap.add_argument("--state", default=str(STATE_PATH), help="seen 상태 파일 경로")
    ap.add_argument("--no-affiliate", action="store_true", help="쿠팡 단축 링크 변환 끄기")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    common.load_dotenv()
    if (os.environ.get("HOTDEAL_DRY_RUN") or "").strip() in ("1", "true", "yes"):
        args.dry_run = True
    log = common.setup_logging(args.verbose)
    cfg = load_config()

    now = datetime.now(KST)

    if not args.dry_run and not configured_channels():
        log.warning("알림 채널이 없습니다(.env의 TELEGRAM_* / DISCORD_WEBHOOK_URL / X_WEBHOOK_URL) "
                    "— 미리보기만 하고 state에 기록하지 않습니다. 채널을 넣으면 그때부터 실제 전송합니다.")
        args.dry_run = True

    state_path = Path(args.state)
    state = read_json(state_path, default={}) or {}
    seen = state.get("seen", {}) if isinstance(state, dict) else {}

    rows = collect(cfg, log, args.feed)
    picked = select(rows, cfg, seen, now, log)
    limit = args.limit if args.limit is not None else int(cfg.get("max_notify_per_run", 5))
    picked = picked[:max(0, limit)]

    results = []
    for idx, item in enumerate(picked, start=1):
        link = enrich_link(item, not args.no_affiliate, log)
        message = format_message(item, link, idx, len(picked))
        channels = notify(message, args.dry_run, log)
        results.append({
            "key": item["key"], "title": item["title"], "shop": item.get("shop"),
            "price_krw": item.get("price_krw"), "link": link,
            "source": item.get("source"), "matched": item.get("matched", []),
            "published": item.get("published"), "channels": channels,
            "message": message,
        })
        if not args.dry_run:
            seen[item["key"]] = {
                "title": item["title"], "link": item.get("link"),
                "price_krw": item.get("price_krw"),
                "first_seen": now.isoformat(timespec="seconds"),
                "source": item.get("source"),
            }

    if not args.dry_run:
        seen = prune_seen(seen, int(cfg.get("seen_retention_days", 30)), now)
        write_json(state_path, {"seen": seen, "updated_at": now.isoformat(timespec="seconds")})

    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "count": len(results), "items": results},
                         ensure_ascii=False, indent=2))
    else:
        if not results:
            print("새로 알릴 핫딜이 없습니다.")
        else:
            print(f"{'[미리보기] ' if args.dry_run else ''}핫딜 {len(results)}건")
            for r in results:
                print("-" * 60)
                print(r["message"])

    log.info("완료 — 알림 %d건 (dry_run=%s)", len(results), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
