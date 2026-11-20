#!/usr/bin/env python3
"""쿠팡 파트너스 Open API 클라이언트 — HMAC-SHA256(CEA) 서명 + 단축 링크 변환 + 골드박스.

쿠팡 파트너스 Open API는 모든 요청에 아래 형식의 Authorization 헤더가 필요합니다.

    Authorization: CEA algorithm=HmacSHA256, access-key={ACCESS_KEY},
                   signed-date={yyMMdd'T'HHmmss'Z'}, signature={hex}

    message  = signed-date + HTTP method + path + query(‘?’ 제외)
    signature = HMAC-SHA256(message, SECRET_KEY)  # hex

키(`COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY`)가 비어 있으면 예외로 죽지 않고
`CoupangKeysMissing`을 올립니다. 호출부(hotdeal_radar 등)는 이를 잡아 안내만 남기고 건너뜁니다.

CLI 사용 예:
    python3 scripts/coupang_api.py check
    python3 scripts/coupang_api.py deeplink https://www.coupang.com/vp/products/7712096941
    python3 scripts/coupang_api.py deeplink <url> --dry-run      # 서명만 만들고 전송 안 함
    python3 scripts/coupang_api.py goldbox --limit 5
    python3 scripts/coupang_api.py search "무선 청소기" --limit 5

표준 라이브러리만 사용합니다(설치할 패키지 없음). python3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_HOST = "https://api-gateway.coupang.com"
DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
GOLDBOX_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/goldbox"
SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"

TIMEOUT = 15
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 2


class CoupangError(RuntimeError):
    """API가 오류를 돌려줬거나 네트워크가 실패했을 때."""


class CoupangKeysMissing(CoupangError):
    """환경변수에 액세스 키/시크릿 키가 없을 때."""


# --------------------------------------------------------------------------- 서명


def read_keys() -> "tuple[str, str]":
    access_key = (os.environ.get("COUPANG_ACCESS_KEY") or "").strip()
    secret_key = (os.environ.get("COUPANG_SECRET_KEY") or "").strip()
    if not access_key or not secret_key:
        raise CoupangKeysMissing(
            "COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 비어 있습니다. "
            ".env.example 을 .env 로 복사해 값을 채우거나, 쿠팡 파트너스 키 없이 돌리려면 "
            "이 단계를 건너뛰세요(호출부가 자동으로 스킵합니다)."
        )
    return access_key, secret_key


def has_keys() -> bool:
    return bool((os.environ.get("COUPANG_ACCESS_KEY") or "").strip()
                and (os.environ.get("COUPANG_SECRET_KEY") or "").strip())


def signed_datetime(now: "datetime | None" = None) -> str:
    """쿠팡이 요구하는 GMT 시각 문자열: yyMMdd'T'HHmmss'Z'."""
    return (now or datetime.now(timezone.utc)).strftime("%y%m%dT%H%M%SZ")


def build_message(method: str, path: str, query: str, datetime_str: str) -> str:
    return datetime_str + method.upper() + path + (query or "")


def sign(method: str, path: str, query: str, secret_key: str, datetime_str: str) -> str:
    message = build_message(method, path, query, datetime_str)
    return hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def build_headers(method: str, path: str, query: str = "",
                  access_key: "str | None" = None, secret_key: "str | None" = None,
                  now: "datetime | None" = None) -> "dict[str, str]":
    if access_key is None or secret_key is None:
        access_key, secret_key = read_keys()
    datetime_str = signed_datetime(now)
    signature = sign(method, path, query, secret_key, datetime_str)
    return {
        "Authorization": (
            f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={datetime_str}, signature={signature}"
        ),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
    }


def build_query(params: "dict | None") -> str:
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return urllib.parse.urlencode(clean)


# --------------------------------------------------------------------------- 호출


def request(method: str, path: str, query: str = "", body: "dict | None" = None,
            dry_run: bool = False) -> dict:
    """서명해서 호출하고 rCode를 확인한 뒤 응답 JSON을 돌려준다."""
    access_key, secret_key = read_keys()
    url = API_HOST + path + (f"?{query}" if query else "")
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")

    if dry_run:
        dt = signed_datetime()
        sig = sign(method, path, query, secret_key, dt)
        return {
            "dry_run": True,
            "method": method.upper(),
            "url": url,
            "signed_date": dt,
            "signature_sha256": hashlib.sha256(sig.encode()).hexdigest()[:16],
            "body": body,
        }

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(
            url,
            data=payload,
            headers=build_headers(method, path, query, access_key, secret_key),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            last_error = CoupangError(f"HTTP {exc.code} {method.upper()} {path} — {detail}")
            if exc.code in RETRY_STATUS and attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = CoupangError(f"{method.upper()} {path} 실패 — {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_error

        rcode = str(data.get("rCode", "0"))
        if rcode not in ("0", "None"):
            raise CoupangError(f"API rCode={rcode} rMessage={data.get('rMessage')!r} — {path}")
        return data

    raise last_error or CoupangError("알 수 없는 실패")


def deeplink(coupang_urls, sub_id: "str | None" = None, dry_run: bool = False) -> list:
    """상품 URL 목록을 파트너스 단축 링크로 바꾼다.

    반환: [{"originalUrl": str, "shortenUrl": str, "landingUrl": str}, …]
    한 번에 최대 20개까지 넣을 수 있다.
    """
    if isinstance(coupang_urls, str):
        coupang_urls = [coupang_urls]
    urls = [u for u in coupang_urls if u]
    if not urls:
        raise ValueError("변환할 쿠팡 URL이 없습니다")
    body = {"coupangUrls": urls}
    sub_id = sub_id or (os.environ.get("COUPANG_SUB_ID") or "").strip()
    if sub_id:
        body["subId"] = sub_id
    data = request("POST", DEEPLINK_PATH, query="", body=body, dry_run=dry_run)
    if data.get("dry_run"):
        return [data]
    return data.get("data") or []


def goldbox(limit: int = 10, sub_id: "str | None" = None, dry_run: bool = False) -> list:
    """골드박스(오늘의 특가) 상품 목록."""
    params = {"subId": sub_id or (os.environ.get("COUPANG_SUB_ID") or "").strip(),
              "limit": limit}
    data = request("GET", GOLDBOX_PATH, query=build_query(params), dry_run=dry_run)
    if data.get("dry_run"):
        return [data]
    return data.get("data") or []


def search(keyword: str, limit: int = 10, sub_id: "str | None" = None,
           dry_run: bool = False) -> list:
    """키워드 상품 검색."""
    params = {"keyword": keyword, "limit": limit,
              "subId": sub_id or (os.environ.get("COUPANG_SUB_ID") or "").strip()}
    data = request("GET", SEARCH_PATH, query=build_query(params), dry_run=dry_run)
    if data.get("dry_run"):
        return [data]
    return data.get("data") or []


def first_shorten_url(coupang_url: str, sub_id: "str | None" = None) -> "str | None":
    """URL 1개를 단축 링크로. 실패하면 None(예외를 밖으로 던지지 않음)."""
    try:
        for row in deeplink(coupang_url, sub_id=sub_id):
            if row.get("shortenUrl"):
                return row["shortenUrl"]
    except CoupangKeysMissing:
        return None
    except CoupangError as exc:
        print(f"[coupang] 단축 링크 변환 실패 — {exc}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- CLI


def _mask(value: str) -> str:
    if not value:
        return "(없음)"
    if len(value) <= 8:
        return value[:2] + "…"
    return f"{value[:4]}…{value[-2:]} ({len(value)}자)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="쿠팡 파트너스 Open API 도구")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("check", help="키 설정 여부와 서명 생성만 확인(네트워크 호출 없음)")

    p_deep = sub.add_parser("deeplink", help="상품 URL → 파트너스 단축 링크")
    p_deep.add_argument("urls", nargs="+")
    p_deep.add_argument("--sub-id", default=None)
    p_deep.add_argument("--dry-run", action="store_true")

    p_gold = sub.add_parser("goldbox", help="골드박스 특가 목록")
    p_gold.add_argument("--limit", type=int, default=10)
    p_gold.add_argument("--sub-id", default=None)
    p_gold.add_argument("--dry-run", action="store_true")

    p_search = sub.add_parser("search", help="키워드 상품 검색")
    p_search.add_argument("keyword")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--sub-id", default=None)
    p_search.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0

    if args.cmd == "check":
        ak = (os.environ.get("COUPANG_ACCESS_KEY") or "").strip()
        sk = (os.environ.get("COUPANG_SECRET_KEY") or "").strip()
        print(f"COUPANG_ACCESS_KEY: {_mask(ak)}")
        print(f"COUPANG_SECRET_KEY: {'(설정됨)' if sk else '(없음)'}")
        print(f"COUPANG_SUB_ID    : {os.environ.get('COUPANG_SUB_ID') or '(없음)'}")
        if not (ak and sk):
            print("\n키가 비어 있습니다 → deeplink·골드박스 호출은 건너뜁니다(다른 기능은 정상 동작).")
            print("키를 채우려면: cp .env.example .env  → 값 입력 → `set -a; . ./.env; set +a`")
            return 0
        sample = build_headers("POST", DEEPLINK_PATH, "", ak, sk)
        print("\n서명 생성 예시(전송 안 함):")
        print("  Authorization:", sample["Authorization"][:80] + "…")
        print("\n실제 호출까지 확인하려면: python3 scripts/coupang_api.py goldbox --limit 3")
        return 0

    try:
        if args.cmd == "deeplink":
            rows = deeplink(args.urls, sub_id=args.sub_id, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps(rows[0], ensure_ascii=False, indent=2))
                return 0
            for row in rows:
                print(f"{row.get('originalUrl')}\n  → {row.get('shortenUrl')}")
        elif args.cmd == "goldbox":
            rows = goldbox(limit=args.limit, sub_id=args.sub_id, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps(rows[0], ensure_ascii=False, indent=2))
                return 0
            for row in rows:
                price = row.get("productPrice")
                print(f"- {row.get('productName')} / {price}원 / {row.get('productUrl')}")
        elif args.cmd == "search":
            rows = search(args.keyword, limit=args.limit, sub_id=args.sub_id, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps(rows[0], ensure_ascii=False, indent=2))
                return 0
            for row in rows:
                print(f"- {row.get('productName')} / {row.get('productPrice')}원 / {row.get('productUrl')}")
    except CoupangKeysMissing as exc:
        print(f"[건너뜀] {exc}", file=sys.stderr)
        return 3
    except CoupangError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
