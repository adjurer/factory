#!/usr/bin/env python3
"""사이클 보고 — 맥미니가 한 주기(기본 30분)의 실행 결과·오류·개선 제안을 `reports/`에 남기고 푸시한다.

이 스크립트가 맥미니 → 유리로 돌아가는 **피드백 통로**입니다.

    ① `git pull --rebase --autostash` 로 최신 글감·지시서(AGENTS.md)를 받는다
    ② 그 주기에 실제로 무슨 일이 있었는지 상태 파일·로그에서 모은다
       (새 글감 처리, codex 실패, 핫딜 레이더, 쿠팡 키, codex/ffmpeg/yt-dlp, 디스크)
    ③ `reports/latest.md`(현재 상태 전체) + `reports/daily/{날짜}.md`(한 줄 요약 누적)를 쓴다
    ④ `reports/` 만 `git add` → commit → push (실패하면 rebase 후 1회 재시도)

역할 경계: 맥미니가 쓰는 것은 `reports/`·`drafts/`·`feed/` 뿐입니다.
`AGENTS.md`·`feed/SCHEMA.md`·스크립트는 유리 몫이므로 이 스크립트는 건드리지 않습니다.
규칙 개선 제안은 `reports/improvements.md`에 쌓고, 유리가 읽어 `AGENTS.md`에 반영합니다.

사용법
    python3 scripts/report_cycle.py                       # pull → 보고 → 커밋·푸시
    python3 scripts/report_cycle.py --no-push             # 로컬에 보고만 (푸시 안 함)
    python3 scripts/report_cycle.py --no-pull --json      # 오프라인 점검 + 기계용 JSON
    python3 scripts/report_cycle.py --interval-min 60     # "이번 주기" 판정 구간을 60분으로
    python3 scripts/report_cycle.py --reports-dir /tmp/r  # 다른 폴더에 써 보기(테스트)

`--dry-run` 은 어떤 파일도 쓰지 않고 보고서 본문만 표준출력으로 보여 줍니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import BOT_DIR, FEED_DIR, REPO_ROOT, have, read_json, redact_home, run, write_json  # noqa: E402

KST = timezone(timedelta(hours=9))
DEFAULT_REPORTS = BOT_DIR / "reports"
CODE_SYNC_STATE = BOT_DIR / "state" / "processed_ids.json"
HOTDEAL_STATE = BOT_DIR / "state" / "seen.json"
REPORT_STATE = BOT_DIR / "state" / "report_state.json"
LOG_DIR = BOT_DIR / "logs"
DAILY_RETENTION_DAYS = 45
# 조용한 주기는 커밋하지 않는다(공개 저장소 커밋 폭주 방지). 바뀐 것이 있으면 바로 올린다.
QUIET_SKIP_MINUTES = 55

PUSH_LABEL = "reports"
LINES_TAIL = 25


# --------------------------------------------------------------------------- git


def rel_to_repo(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def git(args, timeout: int = 120):
    """`git -C REPO_ROOT ...` 실행. (ok, stdout, stderr)"""
    try:
        proc = run(["git", "-C", str(REPO_ROOT), *args], timeout=timeout)
    except RuntimeError as exc:
        return False, "", str(exc)
    return proc.returncode == 0, proc.stdout.rstrip("\n"), proc.stderr.strip()


def git_info() -> dict:
    ok_branch, branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"])
    ok_head, head, _ = git(["log", "-1", "--format=%h%x09%s%x09%cI"])
    parts = head.split("\t") if head else []
    ok_dirty, dirty, _ = git(["status", "--porcelain"])
    tracked_dirty = [ln for ln in dirty.splitlines() if ln.strip()] if ok_dirty else []
    return {
        "branch": branch if ok_branch else "(알 수 없음)",
        "head": parts[0] if parts else "(없음)",
        "head_subject": parts[1] if len(parts) > 1 else "",
        "head_at": parts[2] if len(parts) > 2 else "",
        "dirty": tracked_dirty,
    }


def pull_repo(enabled: bool) -> dict:
    if not enabled:
        return {"ok": True, "detail": "pull 생략(--no-pull)"}
    try:
        proc = run(["git", "-C", str(REPO_ROOT), "pull", "--rebase", "--autostash"], timeout=180)
    except RuntimeError as exc:
        return {"ok": False, "detail": str(exc)}
    out = (proc.stdout or "").strip().splitlines()
    detail = " ".join((out[-1] if out else (proc.stderr or "")).split())[-220:] or "(출력 없음)"
    if proc.returncode != 0:
        return {"ok": False, "detail": detail}
    return {"ok": True, "detail": detail}


# --------------------------------------------------------------------------- 상태 모으기


def feed_status(processed: dict) -> dict:
    index = read_json(FEED_DIR / "index.json", default={}) or {}
    items = index.get("items") or []
    ids = [it.get("id") for it in items if it.get("id")]
    pending = [i for i in ids if i not in processed]
    latest = items[0] if items else {}
    return {
        "total": len(ids),
        "pending": pending,
        "latest_id": latest.get("id"),
        "latest_name": (latest.get("product_name") or "")[:60],
        "latest_at": latest.get("created_at"),
        "index_at": index.get("updated_at"),
    }


def parse_iso(value) -> "datetime | None":
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def codex_state(processed: dict, failed: dict, since: datetime) -> dict:
    in_window = [k for k, v in processed.items()
                 if (parse_iso((v or {}).get("processed_at")) or datetime.min.replace(tzinfo=KST)) >= since]
    out_root = BOT_DIR / "output"
    outs = [p for p in out_root.iterdir() if p.is_dir() and not p.name.startswith("_")] if out_root.exists() else []
    outs.sort(key=lambda p: p.stat().st_mtime)
    last_out = None
    if outs:
        last = outs[-1]
        last_out = {"id": last.name,
                    "at": datetime.fromtimestamp(last.stat().st_mtime, KST).isoformat(timespec="minutes")}
    drafts = BOT_DIR / "drafts"
    draft_ids = sorted(p.name for p in drafts.iterdir() if p.is_dir()) if drafts.exists() else []
    return {
        "processed_total": len(processed),
        "processed_window": in_window,
        "failed": failed,
        "outputs": len(outs),
        "last_output": last_out,
        "drafts": len(draft_ids),
        "last_draft": draft_ids[-1] if draft_ids else None,
    }


def log_tail(path: Path, lines: int = LINES_TAIL) -> "list[str]":
    if not path.exists():
        return []
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return data[-lines:]


def hotdeal_status(seen: dict, since: datetime) -> dict:
    recent = [meta for meta in seen.values()
              if isinstance(meta, dict) and (parse_iso(meta.get("first_seen")) or datetime.min.replace(tzinfo=KST)) >= since]
    state = read_json(HOTDEAL_STATE, default={}) or {}
    updated = state.get("updated_at") if isinstance(state, dict) else None
    log_path = LOG_DIR / "hotdeal-radar.log"
    tail = log_tail(log_path)
    problems = [ln for ln in tail if (" ERROR " in ln or " WARNING " in ln)][-6:]
    return {
        "seen_total": len(seen),
        "seen_window": len(recent),
        "recent_titles": [m.get("title", "")[:70] for m in recent[:3]],
        "updated_at": updated,
        "fed_at": updated or None,
        "log_problems": problems,
        "log_path": str(log_path.relative_to(BOT_DIR)) if log_path.exists() else None,
    }


def cmd_version(cmd: str, args=("--version",), timeout: int = 20) -> "dict":
    path = have(cmd)
    if not path:
        return {"present": False, "path": None, "version": None}
    try:
        proc = run([path, *args], timeout=timeout)
    except RuntimeError as exc:
        return {"present": True, "path": path, "version": None, "error": str(exc)}
    first = ((proc.stdout or proc.stderr or "").strip().splitlines() or [""])[0]
    return {"present": True, "path": path, "version": first[:80] or None}


def deps_status() -> dict:
    codex = cmd_version("codex")
    auth_file = Path.home() / ".codex" / "auth.json"
    codex["auth"] = bool(auth_file.exists()) or bool(os.environ.get("OPENAI_API_KEY"))
    codex["auth_file"] = str(auth_file).replace(str(Path.home()), "~")
    return {
        "python": sys.version.split()[0],
        "python_path": sys.executable.replace(str(Path.home()), "~"),
        "venv": bool(os.environ.get("VIRTUAL_ENV")) or ".venv" in sys.executable,
        "codex": codex,
        "ffmpeg": cmd_version("ffmpeg", ("-version",)),
        "ffprobe": cmd_version("ffprobe", ("-version",)),
        "yt_dlp": cmd_version("yt-dlp", ("--version",)),
        "git": cmd_version("git"),
    }


def env_status() -> dict:
    def filled(name: str) -> bool:
        return bool((os.environ.get(name) or "").strip())
    keys = ["COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "DISCORD_WEBHOOK_URL", "X_WEBHOOK_URL"]
    return {
        "env_file": (BOT_DIR / ".env").exists(),
        "coupang": filled("COUPANG_ACCESS_KEY") and filled("COUPANG_SECRET_KEY"),
        "coupang_access": filled("COUPANG_ACCESS_KEY"),
        "coupang_secret": filled("COUPANG_SECRET_KEY"),
        "telegram": filled("TELEGRAM_BOT_TOKEN") and filled("TELEGRAM_CHAT_ID"),
        "discord": filled("DISCORD_WEBHOOK_URL"),
        "x_relay": filled("X_WEBHOOK_URL"),
        "keys": keys,
        "missing": [k for k in keys if not filled(k)],
    }


def coupang_check() -> "str | None":
    script = BOT_DIR / "scripts" / "coupang_api.py"
    try:
        proc = run([sys.executable, str(script), "check"], timeout=30)
    except RuntimeError:
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    return text[:600] if text else None


def disk_status() -> dict:
    try:
        usage = shutil.disk_usage(str(BOT_DIR))
    except OSError:
        return {}
    total_gb = usage.total / 1024 ** 3
    if total_gb > 20000:      # 컨테이너·네트워크 FS 는 숫자가 의미 없음
        return {}
    return {"free_gb": round(usage.free / 1024 ** 3, 1),
            "total_gb": round(total_gb, 1),
            "used_pct": round(usage.used / usage.total * 100)}


def agents_doc() -> dict:
    path = BOT_DIR / "AGENTS.md"
    if not path.exists():
        return {"present": False}
    ok, blob, _ = git(["hash-object", "macmini-codex-bot/AGENTS.md"])
    at = datetime.fromtimestamp(path.stat().st_mtime, KST).isoformat(timespec="minutes")
    version = "(버전 표기 없음)"
    for line in path.read_text(encoding="utf-8").splitlines():
        if "지시서 버전" in line:
            version = re.sub(r"[*`]", "", line.split("지시서 버전:", 1)[-1]).split("·")[0].strip()
            break
    return {"present": True, "sha": (blob or "")[:8] or "(미커밋)", "mtime": at, "version": version}


def constitution_doc() -> dict:
    path = REPO_ROOT / "CONSTITUTION.md"
    if not path.exists():
        return {"present": False}
    ok, blob, _ = git(["hash-object", "CONSTITUTION.md"])
    version = "(버전 표기 없음)"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- 제정:"):
            version = re.sub(r"[*`]", "", line.split("제정:", 1)[-1]).strip()
            break
    return {"present": True, "sha": (blob or "")[:8] or "(미커밋)", "version": version}


def improvements_tail(reports_dir: "Path | None" = None) -> "list[str]":
    path = (reports_dir or DEFAULT_REPORTS) / "improvements.md"
    if not path.exists():
        return []
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return data[-15:]


def digest_of(stats: dict) -> str:
    """시각·디스크처럼 매번 변하는 값을 빼고 '내용'만 해시한다."""
    codex = stats["codex"]
    payload = {
        "agents": stats["agents"].get("sha"),
        "constitution": stats.get("constitution", {}).get("sha"),
        "feed_total": stats["feed"]["total"],
        "feed_pending": stats["feed"]["pending"],
        "feed_latest": stats["feed"]["latest_id"],
        "processed_total": codex["processed_total"],
        "outputs": codex["outputs"],
        "failed": sorted(codex["failed"].keys()),
        "drafts": codex["drafts"],
        "hotdeal_total": stats["hotdeal"]["seen_total"],
        "env": [stats["env"][k] for k in ("coupang", "telegram", "discord", "x_relay")],
        "tools": [stats["deps"][k]["present"] for k in ("codex", "ffmpeg", "yt_dlp")],
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def should_skip(stats: dict) -> "tuple[bool, str]":
    prev = read_json(REPORT_STATE, default={}) or {}
    if not isinstance(prev, dict) or not prev.get("digest"):
        return False, "첫 보고"
    if prev.get("digest") != stats["digest"]:
        return False, "내용 변경"
    last = parse_iso(prev.get("at"))
    if last is None:
        return False, "이전 기록 읽기 실패"
    age = (stats["now"] - last).total_seconds() / 60
    if age < QUIET_SKIP_MINUTES:
        return True, f"변경 없음({age:.0f}분 전 보고)"
    return False, "정기 보고"


# --------------------------------------------------------------------------- 제안 만들기


def build_suggestions(stats: dict) -> "list[str]":
    out = []
    deps = stats["deps"]
    env = stats["env"]
    if not stats.get("constitution", {}).get("present"):
        out.append("헌법(CONSTITUTION.md)이 없습니다 → 유리에게 알림(모든 AI가 먼저 읽어야 하는 문서)")
    if not deps["codex"]["present"]:
        out.append("codex CLI 없음 → `npm i -g @openai/codex && codex login` (초안 자동 생성이 멈춰 있습니다)")
    elif not deps["codex"]["auth"]:
        out.append("codex 로그인 흔적 없음 → 터미널에서 `codex login` 한 번")
    if not env["coupang"]:
        out.append("쿠팡 파트너스 키 비어 있음 → `.env`의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY "
                   "(파트너스 > 도구 > Open API 관리)")
    if not deps["ffmpeg"]["present"]:
        out.append("ffmpeg 없음 → `brew install ffmpeg` (720x1280 클립·프레임 검사 불가)")
    if not deps["yt_dlp"]["present"]:
        out.append("yt-dlp 없음 → `brew install yt-dlp` (클립 원본 다시 받기 불가)")
    if not (env["telegram"] or env["discord"] or env["x_relay"]):
        out.append("알림 채널 없음 → 핫딜은 미리보기만 돕니다(텔레그램/Discord/X 중 하나)")
    feed = stats["feed"]
    if feed["pending"]:
        out.append(f"미처리 글감 {len(feed['pending'])}건 → 다음 주기 codex_sync가 처리: "
                   + ", ".join(feed["pending"][:5]))
    if stats["codex"]["failed"]:
        ids = ", ".join(list(stats["codex"]["failed"].keys())[:5])
        out.append(f"codex 실패 재시도 대기 {len(stats['codex']['failed'])}건: {ids}")
    if stats["hotdeal"]["log_problems"]:
        out.append("핫딜 로그에 경고/오류 → `logs/hotdeal-radar.log` 확인 (막힌 피드는 config/feeds.json에서 끄기)")
    if stats["disk"] and stats["disk"].get("free_gb", 99) < 5:
        out.append(f"디스크 여유 {stats['disk']['free_gb']}GB → output/·logs/ 정리 필요")
    others = [ln for ln in stats["git"]["dirty"]
              if not ln.endswith("macmini-codex-bot/reports") and "macmini-codex-bot/reports/" not in ln]
    if others:
        out.append(f"보고 밖 파일이 수정돼 있음 {len(others)}건 → 맥미니는 reports/drafts/feed만 써야 합니다: "
                   + ", ".join(ln[3:] for ln in others[:4]))
    if not out:
        out.append("없음 — 이번 주기 점검 항목 모두 정상")
    return out


# --------------------------------------------------------------------------- 보고서


def yes_no(value: bool) -> str:
    return "OK" if value else "없음"


def gb(value) -> str:
    """디스크 용량 표기 — 아주 큰 값(컨테이너)은 정수로 줄인다."""
    if value is None:
        return "?"
    return f"{value:.0f}" if value >= 1000 else f"{value:.1f}"


def render(stats: dict) -> str:
    now = stats["now"]
    git_i = stats["git"]
    feed = stats["feed"]
    codex = stats["codex"]
    deps = stats["deps"]
    env = stats["env"]
    hot = stats["hotdeal"]
    agents = stats["agents"]
    disk = stats["disk"]

    codex_ver = deps["codex"].get("version") or ("설치됨" if deps["codex"]["present"] else "없음")
    auth = "로그인 확인" if deps["codex"]["auth"] else "로그인 필요"
    pull_line = ("성공 — " + redact_home(stats["pull"]["detail"])) if stats["pull"]["ok"] \
        else ("실패 — " + redact_home(stats["pull"]["detail"]))
    last_out = codex["last_output"]["id"] if codex["last_output"] else "없음"
    window_ids = ", ".join(codex["processed_window"]) if codex["processed_window"] else "0건"
    seen_recent = (f"{hot['seen_window']}건 · " + " / ".join(hot["recent_titles"])) if hot["recent_titles"] \
        else f"{hot['seen_window']}건"

    rows = [
        ("저장소", f"adjurer/factory @ `{git_i['branch']}` `{git_i['head']}`"),
        ("마지막 커밋", (git_i["head_subject"] or "-")[:70]),
        ("git pull(--rebase)", pull_line),
        ("지시서(AGENTS.md)", f"`{agents.get('sha', '-')}` · {agents.get('version', '-')} · {agents.get('mtime', '-')}"),
        ("헌법(CONSTITUTION.md)", f"`{stats['constitution'].get('sha', '-')}` · {stats['constitution'].get('version', '-')}"),
        ("글감", f"전체 {feed['total']}건 · 미처리 {len(feed['pending'])}건 · 최신 {feed['latest_id'] or '-'}"),
        ("이 주기 처리", window_ids),
        ("codex_sync", f"누적 {codex['processed_total']}건 · 산출물 {codex['outputs']}개 · 마지막 {last_out}"),
        ("drafts(공유 초안)", f"{codex['drafts']}건 · 최근 {codex['last_draft'] or '-'}"),
        ("핫딜 레이더", f"누적 {hot['seen_total']}건 · 이 주기 {seen_recent}"),
        ("쿠팡 키", f"ACCESS {'설정' if env['coupang_access'] else '없음'} · "
                  f"SECRET {'설정' if env['coupang_secret'] else '없음'}"),
        ("알림 채널", " · ".join(filter(None, [
            "텔레그램" if env["telegram"] else "", "Discord" if env["discord"] else "",
            "X 릴레이" if env["x_relay"] else ""])) or "없음(미리보기)"),
        ("codex CLI", f"{codex_ver} · {auth}"),
        ("ffmpeg / yt-dlp", f"{yes_no(deps['ffmpeg']['present'])} / {yes_no(deps['yt_dlp']['present'])}"),
        ("파이썬", f"{deps['python']} ({deps['python_path']})"),
        ("디스크", f"여유 {gb(disk.get('free_gb'))}GB / {gb(disk.get('total_gb'))}GB ({disk.get('used_pct', '?')}% 사용)"),
    ]
    width = max(len(k) for k, _ in rows)

    lines = [
        f"# 맥미니 사이클 보고 — {now.strftime('%Y-%m-%d %H:%M')} KST",
        "",
        "> 맥미니(Codex)가 자동으로 남기는 실행 보고입니다. **이 파일은 맥미니만 씁니다.**",
        "> 유리: 이 파일과 `reports/improvements.md`만 읽으면 맥미니 상태를 알 수 있습니다.",
        "> 규칙(`AGENTS.md`·`feed/SCHEMA.md`·스크립트)은 유리 몫이고, 맥미니는 제안만 `improvements.md`에 씁니다.",
        "",
        "## 1. 이번 주기 요약",
        "",
        "| 항목 | 값 |",
        "|---|---|",
    ]
    for key, value in rows:
        lines.append(f"| {key} | {value} |")

    lines += ["", "## 2. 처리 대기·실패", ""]
    if feed["pending"]:
        lines.append(f"- 미처리 글감: {', '.join(feed['pending'][:10])}")
    if codex["failed"]:
        for item_id, meta in list(codex["failed"].items())[:5]:
            err = (meta or {}).get("last_error", "")
            lines.append(f"- codex 실패 `{item_id}` — {str(err)[:120]} ({(meta or {}).get('attempts', '?')}회)")
    if not feed["pending"] and not codex["failed"]:
        lines.append("- 없음")
    if feed["latest_id"]:
        lines += ["", f"최신 글감: **{feed['latest_id']}** — {feed['latest_name']} ({feed['latest_at']})"]

    lines += ["", "## 3. 핫딜 레이더", "",
              f"- 누적 본 글: {hot['seen_total']}건 · 이 주기 신규 {hot['seen_window']}건",
              f"- 마지막 실행: {hot['fed_at'] or '(기록 없음)'}",
              f"- 로그: `{hot['log_path'] or 'logs/hotdeal-radar.log 없음'}`"]
    if hot["log_problems"]:
        lines += ["", "최근 경고/오류:", "```"]
        lines += [redact_home(ln)[:200] for ln in hot["log_problems"]]
        lines += ["```"]

    lines += ["", "## 4. 환경 점검", "",
              f"- codex: {codex_ver} · {auth} ({deps['codex'].get('auth_file', '-')})",
              f"- ffmpeg: {deps['ffmpeg'].get('version') or '없음'} · "
              f"ffprobe: {yes_no(deps['ffprobe']['present'])} · yt-dlp: {deps['yt_dlp'].get('version') or '없음'}",
              f"- .env: {'있음' if env['env_file'] else '없음'} · 비어 있는 키 {len(env['missing'])}개"
              + (f" ({', '.join(env['missing'])})" if env["missing"] else ""),
              f"- 저장소 변경 파일: {len(git_i['dirty'])}건"
              + (f" ({', '.join(ln[3:] for ln in git_i['dirty'][:5])})" if git_i["dirty"] else "")]
    if stats.get("coupang_check"):
        lines += ["", "쿠팡 키 확인 출력:", "```", redact_home(stats["coupang_check"]), "```"]

    lines += ["", "## 5. 맥미니 제안 (다음 주기 할 일)", ""]
    lines += [f"- {s}" for s in stats["suggestions"]]

    lines += ["", "## 6. 유리에게 (규칙 개선 제안)", "",
              "`reports/improvements.md`에 날짜별로 쌓습니다. 유리는 이걸 읽고 `AGENTS.md`·`feed/SCHEMA.md`를 고칩니다."]
    tail = improvements_tail(stats.get("reports_dir"))
    if tail:
        lines += ["", "```"] + [redact_home(ln) for ln in tail] + ["```"]

    lines += ["", "---", "",
              f"이 보고는 `scripts/report_cycle.py`가 {now.strftime('%H:%M')} KST에 자동 생성했습니다. "
              f"(주기 {stats['interval']}분 기준)"]
    return "\n".join(lines).rstrip() + "\n"


def render_daily_block(stats: dict) -> str:
    now = stats["now"]
    feed = stats["feed"]
    codex = stats["codex"]
    hot = stats["hotdeal"]
    next_id = feed["pending"][0] if feed["pending"] else "-"
    return (f"\n## {now.strftime('%H:%M')} KST — 주기 보고\n"
            f"- 글감 전체 {feed['total']} · 미처리 {len(feed['pending'])} · 이 주기 처리 {len(codex['processed_window'])}\n"
            f"- codex 실패 {len(codex['failed'])} · 핫딜 신규 {hot['seen_window']} · drafts {codex['drafts']}\n"
            f"- 다음 처리 대상: {next_id}\n")


def prune_daily(reports_dir: Path, now: datetime) -> "list[str]":
    daily = reports_dir / "daily"
    removed = []
    if not daily.exists():
        return removed
    cutoff = now.date() - timedelta(days=DAILY_RETENTION_DAYS)
    for path in daily.glob("*.md"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def ensure_improvements(reports_dir: Path) -> None:
    path = reports_dir / "improvements.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# 맥미니 → 유리 규칙 개선 제안\n\n"
                        "맥미니가 주기 보고 중 발견한 것을 날짜별로 한 줄씩 쌓습니다.\n"
                        "유리는 이 파일을 읽고 `AGENTS.md`·`feed/SCHEMA.md`·`scripts/`를 고칩니다.\n"
                        "맥미니는 이 파일에만 쓰고, 규칙 파일 자체는 고치지 않습니다.\n"
                        "```\n"
                        "## 2026-11-23\n"
                        "- (예) 뽐뿌 RSS가 오후에 403을 자주 냅니다 → feeds.json에서 15시 이후 건너뛰기 제안\n"
                        "```\n", encoding="utf-8")


# --------------------------------------------------------------------------- 커밋·푸시


def ensure_identity() -> None:
    """커밋 신원이 없으면 저장소 로컬로만 설정한다(개인 메일을 노출하지 않는 noreply 주소)."""
    ok, email, _ = git(["config", "user.email"])
    if ok and email.strip():
        return
    git(["config", "user.email", "59545988+adjurer@users.noreply.github.com"])
    git(["config", "user.name", "macmini-codex-bot"])


def commit_push(reports_dir: Path, message: str, enable: bool, dry_run: bool) -> dict:
    if dry_run:
        return {"status": "dry-run", "detail": "커밋·푸시 안 함"}
    try:
        rel = str(reports_dir.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return {"status": "skip", "detail": "저장소 밖 reports 폴더 — 커밋하지 않습니다"}
    if not enable:
        return {"status": "skip", "detail": "--no-push"}

    ensure_identity()
    ok, _, err = git(["add", "-A", "--", rel])
    if not ok:
        return {"status": "error", "detail": f"git add 실패: {err[:160]}"}
    ok_diff, _, _ = git(["diff", "--cached", "--quiet", "--", rel])
    if ok_diff:
        return {"status": "no-change", "detail": "커밋할 변경 없음"}
    ok, out, err = git(["commit", "-m", message, "--", rel])
    if not ok:
        return {"status": "error", "detail": f"commit 실패: {(err or out)[:160]}"}

    ok, out, err = git(["push", "origin", "HEAD"], timeout=180)
    if not ok:
        # 원격이 앞서 있으면 rebase 후 1회만 재시도
        git(["pull", "--rebase", "--autostash"], timeout=180)
        ok, out, err = git(["push", "origin", "HEAD"], timeout=180)
        if not ok:
            return {"status": "push-failed", "detail": (err or out)[:200]}
        return {"status": "pushed", "detail": "rebase 후 푸시 성공"}
    return {"status": "pushed", "detail": (out.splitlines()[-1] if out else "ok")[:160]}


# --------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="맥미니 사이클 보고 → reports/ 커밋·푸시")
    ap.add_argument("--no-pull", action="store_true", help="git pull 생략")
    ap.add_argument("--no-push", action="store_true", help="커밋·푸시 생략(파일만 씀)")
    ap.add_argument("--dry-run", action="store_true", help="아무 파일도 쓰지 않고 본문만 출력")
    ap.add_argument("--json", action="store_true", help="기계용 JSON 요약 출력")
    ap.add_argument("--interval-min", type=int, default=30, help="'이번 주기' 판정 구간(분)")
    ap.add_argument("--force", action="store_true", help="변경이 없어도 보고서를 쓰고 커밋한다")
    ap.add_argument("--reports-dir", default=str(DEFAULT_REPORTS), help="보고서 폴더(테스트용)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    common.load_dotenv()
    log = common.setup_logging(args.verbose)
    reports_dir = Path(args.reports_dir)
    now = datetime.now(KST)
    since = now - timedelta(minutes=max(1, args.interval_min))

    with common.repo_lock(wait_sec=600) as got:
        if not got:
            log.warning("다른 실행이 저장소를 잡고 있습니다 — 이번 주기 보고는 건너뜁니다")
            print(json.dumps({"skipped": "locked"}, ensure_ascii=False) if args.json else "다른 실행 중 — 건너뜀")
            return 0

        pull = pull_repo(not args.no_pull)
        if not pull["ok"]:
            log.warning("pull 실패 — %s (로컬 상태로 보고)", pull["detail"])

        state = read_json(CODE_SYNC_STATE, default={}) or {}
        processed = state.get("ids", {}) if isinstance(state, dict) else {}
        failed = state.get("failed", {}) if isinstance(state, dict) else {}
        seen_state = read_json(HOTDEAL_STATE, default={}) or {}
        seen = seen_state.get("seen", {}) if isinstance(seen_state, dict) else {}

        stats = {
            "now": now,
            "interval": args.interval_min,
            "pull": pull,
            "git": git_info(),
            "feed": feed_status(processed),
            "codex": codex_state(processed, failed, since),
            "hotdeal": hotdeal_status(seen, since),
            "deps": deps_status(),
            "env": env_status(),
            "agents": agents_doc(),
            "constitution": constitution_doc(),
            "disk": disk_status(),
            "coupang_check": coupang_check(),
            "reports_dir": reports_dir,
        }
        stats["suggestions"] = build_suggestions(stats)
        stats["digest"] = digest_of(stats)

        if not args.dry_run and not args.force:
            skip, why = should_skip(stats)
            if skip:
                log.info("보고 생략 — %s", why)
                if args.json:
                    print(json.dumps({"skipped": why, "digest": stats["digest"]}, ensure_ascii=False))
                else:
                    print(f"보고 생략 — {why} (바뀐 것이 있으면 바로 올립니다)")
                return 0

        body = render(stats)
        if args.dry_run:
            print(body)
            if args.json:
                print(json.dumps({"dry_run": True, "feed": stats["feed"], "suggestions": stats["suggestions"]},
                                 ensure_ascii=False, indent=2))
            return 0

        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "daily").mkdir(exist_ok=True)
        ensure_improvements(reports_dir)
        (reports_dir / "latest.md").write_text(body, encoding="utf-8")
        daily_path = reports_dir / "daily" / f"{now.strftime('%Y-%m-%d')}.md"
        if not daily_path.exists():
            daily_path.write_text(f"# 맥미니 일별 로그 — {now.strftime('%Y-%m-%d')}\n", encoding="utf-8")
        with daily_path.open("a", encoding="utf-8") as fp:
            fp.write(render_daily_block(stats))
        removed = prune_daily(reports_dir, now)

        message = (f"report: {now.strftime('%Y-%m-%d %H:%M')} 맥미니 사이클 — "
                   f"글감 미처리 {len(stats['feed']['pending'])} · 초안 {len(stats['codex']['processed_window'])} · "
                   f"실패 {len(stats['codex']['failed'])}")
        push = commit_push(reports_dir, message, enable=not args.no_push, dry_run=False)
        if push["status"] in ("pushed", "no-change", "skip", "dry-run"):
            write_json(REPORT_STATE, {"digest": stats["digest"],
                                      "at": now.isoformat(timespec="seconds"),
                                      "push": push["status"]})

        log.info("보고 완료 — %s (%s)", rel_to_repo(reports_dir),
                 push["status"] + (f": {push['detail']}" if push.get("detail") else ""))
        if removed:
            log.info("오래된 일별 로그 정리 %d개", len(removed))

    if args.json:
        print(json.dumps({
            "at": now.isoformat(timespec="seconds"),
            "pull": pull,
            "feed": {"total": stats["feed"]["total"], "pending": stats["feed"]["pending"],
                     "latest": stats["feed"]["latest_id"]},
            "processed_window": stats["codex"]["processed_window"],
            "failed": list(stats["codex"]["failed"].keys()),
            "drafts": stats["codex"]["drafts"],
            "hotdeal": {"seen_total": stats["hotdeal"]["seen_total"], "window": stats["hotdeal"]["seen_window"]},
            "push": push,
            "suggestions": stats["suggestions"],
            "report": str(reports_dir / "latest.md"),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"보고 완료 → {reports_dir / 'latest.md'}")
        print(f"- 글감 전체 {stats['feed']['total']} · 미처리 {len(stats['feed']['pending'])}"
              f" · 이 주기 처리 {len(stats['codex']['processed_window'])}")
        print(f"- 푸시: {push['status']}" + (f" ({push['detail']})" if push.get("detail") else ""))
        for s in stats["suggestions"][:5]:
            print(f"  · {s}")
    return 0 if push["status"] in ("pushed", "no-change", "skip", "dry-run") else 4


if __name__ == "__main__":
    raise SystemExit(main())
