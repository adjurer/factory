#!/usr/bin/env python3
"""스크립트 공용 유틸 — .env 로딩, HTTP, JSON 입출력, 로깅.

이 모듈은 표준 라이브러리만 씁니다. macmini의 `/usr/bin/python3`(3.9)에서도 동작하도록
`from __future__ import annotations` 를 사용합니다.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent          # …/macmini-codex-bot
REPO_ROOT = BOT_DIR.parent                                # …/factory
FEED_DIR = REPO_ROOT / "feed"

# 뽐뿌 등 일부 사이트는 기본 UA("Mozilla/5.0")를 403으로 막습니다. 브라우저 UA를 기본으로 씁니다.
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


# --------------------------------------------------------------------------- .env


def load_dotenv(path: "Path | str | None" = None, override: bool = False) -> "dict[str, str]":
    """KEY=VALUE 형식의 .env 를 읽어 os.environ에 채운다.

    - `#`으로 시작하는 줄과 빈 줄은 무시
    - 값 양끝의 따옴표 제거
    - 이미 있는 환경변수는 override=True 일 때만 덮어쓴다(launchd 환경변수 우선)
    """
    env_path = Path(path) if path else BOT_DIR / ".env"
    loaded: "dict[str, str]" = {}
    if not env_path.exists():
        return loaded
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


# --------------------------------------------------------------------------- HTTP


def http_get(url: str, timeout: int = 20, headers: "dict[str, str] | None" = None,
             retries: int = 1) -> str:
    """GET 요청 본문을 문자열로 돌려준다. 실패 시 RuntimeError."""
    hdrs = {"User-Agent": DEFAULT_UA,
            "Accept": "application/rss+xml,application/xml;q=0.9,text/html;q=0.8,*/*;q=0.7",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    if headers:
        hdrs.update(headers)
    last: "Exception | None" = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET 실패 {url} — {last}")


def http_post_json(url: str, payload: dict, timeout: int = 20) -> "tuple[int, str]":
    """JSON POST. (status_code, body) 를 돌려준다. HTTP 오류도 상태코드로 돌려준다."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": DEFAULT_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]


# --------------------------------------------------------------------------- JSON


def read_json(path: "Path | str", default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: "Path | str", obj) -> None:
    """임시 파일에 쓴 뒤 교체 — 실행 중 죽어도 반쯤 쓰인 JSON이 남지 않는다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --------------------------------------------------------------------------- 로깅


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("factory")
