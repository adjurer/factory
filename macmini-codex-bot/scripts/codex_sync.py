#!/usr/bin/env python3
"""코덱스 동기화 — 새 글감(feed)을 맥미니의 Codex 작업물로 바꾼다.

동작 순서
  1) `git pull --ff-only` 로 저장소를 최신으로 (--no-pull 로 생략)
  2) feed/index.json 을 읽어 아직 처리 안 한 id 를 state/processed_ids.json 과 비교
  3) 새 글감마다 output/{id}/ 에 아래를 만든다
       - manifest.json       글감 원본 + 생성 시각
       - codex_prompt.md     Codex에 넘길 지시서(AGENTS.md 규칙 포함)
       - captions.srt        숏폼 자막 초안(클립 길이에 맞춰 배분)
       - shorts_package.md   제목·본문·첫 댓글·해시태그·업로드 순서 체크리스트
  4) --run-codex 를 주면 로컬 `codex` CLI를 불러 codex_output.md 까지 채운다
     (CLI가 없거나 실패하면 템플릿만 남기고 다음 실행에서 다시 시도)

사용법
    python3 scripts/codex_sync.py --dry-run            # pull·상태저장·codex 실행 없이 초안만
    python3 scripts/codex_sync.py --run-codex          # 실제 처리(+코덱스 호출)
    python3 scripts/codex_sync.py --dry-run --id 20261120_1700
    python3 scripts/codex_sync.py --dry-run --json

`--dry-run`은 state/processed_ids.json 을 쓰지 않습니다(다음 실행에서 다시 만들어집니다).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import BOT_DIR, FEED_DIR, REPO_ROOT, read_json, write_json  # noqa: E402

KST = timezone(timedelta(hours=9))
STATE_PATH = BOT_DIR / "state" / "processed_ids.json"
OUT_ROOT = BOT_DIR / "output"
DEFAULT_LIMIT = 5
DEFAULT_CLIP_SEC = 20.0



# --------------------------------------------------------------------------- 글감 읽기


def pull_repo(log, enabled: bool) -> "tuple[bool, str]":
    if not enabled:
        return True, "pull 생략(--no-pull 또는 --dry-run)"
    try:
        proc = subprocess.run(["git", "-C", str(REPO_ROOT), "pull", "--ff-only"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("git pull 실패 — %s (로컬 feed로 계속)", exc)
        return False, str(exc)
    out = (proc.stdout or "").strip().splitlines()
    detail = " ".join((proc.stderr or proc.stdout or "").split())[-200:] or "(출력 없음)"
    if proc.returncode != 0:
        log.warning("git pull 실패(exit %s) — %s (로컬 feed로 계속)", proc.returncode, detail)
        return False, detail
    log.info("git pull — %s", (out[-1] if out else "변경 없음"))
    return True, detail


def load_feed(log) -> "list[dict]":
    """index.json 우선, 없으면 날짜 폴더를 직접 훑는다."""
    index = read_json(FEED_DIR / "index.json")
    entries = []
    if isinstance(index, dict) and index.get("items"):
        for entry in index["items"]:
            path = REPO_ROOT / entry["path"] if entry.get("path") else None
            item = read_json(path) if path else None
            if isinstance(item, dict):
                entries.append(item)
            else:
                log.warning("글감 파일을 읽지 못했습니다 — %s", entry.get("path"))
    if entries:
        return entries

    log.warning("index.json 이 없습니다 — feed 폴더를 직접 스캔합니다")
    for path in sorted(FEED_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/*.json")):
        item = read_json(path)
        if isinstance(item, dict) and item.get("id"):
            entries.append(item)
    return entries


def sort_by_created(items: "list[dict]") -> "list[dict]":
    return sorted(items, key=lambda it: str(it.get("created_at", "")))


# --------------------------------------------------------------------------- 자막


def srt_stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    millis = int( round((seconds - int(seconds)) * 1000) )
    if millis == 1000:            # 반올림 경계 보정
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def hook_lines(text: str) -> "list[str]":
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def build_srt(item: dict, max_cues: int = 5) -> str:
    """hooks.threads 의 줄을 클립 길이에 맞춰 배분한 자막 초안.

    ※ 영상 음성을 받아쓴 것이 아니라 카피 문구를 자막 후보로 배치한 '초안'입니다.
      실제 자막은 codex/사람이 영상과 대조해 고쳐야 합니다(그래서 헤더에 NOTE를 남깁니다).
    """
    duration = float((item.get("video") or {}).get("duration_sec") or DEFAULT_CLIP_SEC)
    lines = hook_lines((item.get("hooks") or {}).get("threads", ""))[:max_cues]
    if not lines:
        lines = [item.get("product_name", "상품")]

    weights = [max(len(line), 8) for line in lines]
    # 자막은 첫 줄(후킹 구간)이 조금 더 오래 머무르게 가중치를 준다
    weights = [w * (1.15 if i == 0 else 1.0) for i, w in enumerate(weights)]
    total_weight = sum(weights)

    cues = []
    cursor = 0.0
    for idx, (line, weight) in enumerate(zip(lines, weights), start=1):
        span = max(1.2, duration * weight / total_weight)
        start = cursor
        end = min(duration, start + span)
        if idx == len(lines):
            end = duration
        cues.append((idx, start, end, line))
        cursor = end
        if cursor >= duration:
            break

    out: "list[str]" = []
    for idx, start, end, line in cues:
        out.append(str(idx))
        out.append(f"{srt_stamp(start)} --> {srt_stamp(end)}")
        out.append(line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- 문서


def build_prompt(item: dict, out_dir: Path) -> str:
    payload = json.dumps(item, ensure_ascii=False, indent=2)
    title = item.get("product_name", "글감")
    return f"""# Codex 작업 지시 — {item.get('id')} ({title})

너는 맥미니에서 도는 콘텐츠 제작 도우미다. 아래 글감 1건으로 **추가 콘텐츠 4종**을 만들어
이 폴더(`{out_dir.name}/`)에 저장한다. 저장소 규칙은 `macmini-codex-bot/AGENTS.md`를 따른다.

## 이번 글감 (feed JSON 원본 — 여기 적힌 수치만 사실로 취급)
```json
{payload}
```

## 만들 것 (파일명 고정)
1. `shorts_script.md` — 숏폼 스크립트
   - 훅 3안(각 2줄, 입말 반말, 호들갑 톤), 20초 나레이션/자막 타임라인(0~3s / 3~8s / 8~15s / 15~20s)
   - 직접 촬영할 경우의 컷 리스트(핸드헬드 3컷 이상)와 촬영 팁
2. `captions.srt` — 위 `captions.srt` 초안을 영상 길이에 맞게 다듬기(큐 5개 이하, 한 줄 20자 안팎)
3. `blog_draft.md` — 그루테크 블로그 확장 글
   - H1 + H2 3~4개, 900~1,300자, 정보형 존댓말(~습니다)
   - 도입(문제) → 실제 사용 관점 → 스펙 표 → 주의사항 → 구매 링크 → 대가성 문구
4. `hotdeal_card.md` — 카드뉴스 5장 문구(표지 / 문제 / 제품 / 스펙·주의 / 링크·대가성)

## 절대 규칙
- **JSON에 없는 가격·수치·후기·판매량을 만들지 않는다.** 모르면 "확인 필요"라고 쓴다.
- 영상은 남의 크리에이터 것이다. "직접 촬영" 또는 "허락+크레딧" 없이 재업로드를 전제로 쓰지 않는다.
- 대가성 문구는 `disclosure` 값을 **그대로** 넣는다(문구 변형 금지).
- `caveats` 항목 중 하나라도 빠뜨리면 안 된다(특히 영상-상품 불일치, 유선/무선, A/S).
- 개인정보·계정·API 키를 파일에 쓰지 않는다. 결제·구매·게시는 하지 않는다.
- 말투: 숏폼 자막은 짧은 입말, 블로그는 정보형. "AI 말투"(~할 수 있습니다만, 결론적으로,
  다양한 측면에서) 금지. 광고 문구 남발 금지.

## 마지막에
`codex_output.md` 에 무엇을 만들었는지 5줄 이내로 요약하고, 확인이 필요한 항목을 적는다.
"""


def build_package(item: dict) -> str:
    hooks = item.get("hooks") or {}
    pub = item.get("published_urls") or {}
    tags = item.get("tags") or []
    caveats = item.get("caveats") or []
    product = item.get("product") or {}
    ali = item.get("ali") or {}
    video = item.get("video") or {}

    price = item.get("price_krw")
    list_price = item.get("list_price_krw")
    price_line = f"{price:,}원" if isinstance(price, int) else "가격 확인 필요"
    if isinstance(list_price, int) and isinstance(price, int):
        price_line += f" (정가 {list_price:,}원, {item.get('discount_rate')}%↓)"

    hash_tags = " ".join("#" + t.replace(" ", "") for t in tags) + " #그루테크"
    threads_url = pub.get("threads") or "미게시"
    x_url = pub.get("x") or "미게시"

    lines = [
        f"# 숏폼 패키지 — {item.get('id')}",
        "",
        f"- 상품: **{item.get('product_name')}**",
        f"- 가격: **{price_line}**",
        f"- 쿠팡: {item.get('coupang_short_url')}",
    ]
    if item.get("ali_short_url"):
        lines.append(f"- 알리: {item['ali_short_url']}")
    if product:
        spec_bits = [f"브랜드 {product.get('brand')}", f"모델 {product.get('model')}"]
        if product.get("review_count"):
            spec_bits.append(f"리뷰 {product['review_count']}건")
            if product.get("rating"):
                spec_bits.append(f"평점 {product['rating']}")
        lines.append("- 스펙: " + " · ".join(spec_bits))
    if video:
        lines.append(f"- 클립: {video.get('duration_sec')}초 {video.get('resolution')} "
                     f"({video.get('workspace_path')})")
    lines.append(f"- 게시 기록: 스레드 {threads_url} / X {x_url}")
    lines.append("- 자막: `captions.srt`는 영상 음성 전사가 아닌 카피 기반 초안 — 게시 전 대조 필요")

    lines += ["", "## 1) 제목 후보", ""]
    label = (tags[0] if tags else (item.get("product_name") or "제품").split("(")[0].strip()[:20])
    lines += [f"1. {label} {price_line} 후기 정리 (스펙·주의)",
              f"2. {label}, 이 가격이면 살 만할까? {price_line}",
              f"3. {label} 살 때 꼭 보셔야 할 3가지"]

    lines += ["", "## 2) 스레드 본문", "", "```", hooks.get("threads", "").strip(), "```"]
    lines += ["", "## 3) X 본문 (280자 이내)", "", "```", hooks.get("x", "").strip(), "```"]
    lines += ["", "## 4) 인스타 릴스 캡션(초안)", "", "```",
              hooks.get("instagram_reels", "").strip(), "```"]
    if "#" not in hooks.get("instagram_reels", ""):
        lines += ["", hash_tags]
    lines += ["", "## 5) 첫 댓글", "", "```", item.get("first_comment", "").strip(), "```"]
    lines += ["", "## 6) 올리기 순서", "",
              "1. 영상 업로드 → 100% 완료 확인 (스레드 720×1280 경량본 권장, X는 업로드 실패 시 재시도)",
              "2. 본문 붙여넣기 → 카드 미리보기 확인(깨지면 링크 텍스트만)",
              "3. 첫 댓글 달기(링크 + 대가성 문구 포함)",
              "4. X는 280자 제한 → 상품·가격·링크 / 스펙·주의·대가성 2개 답글로 분할",
              "5. 게시 후 스크린샷 + URL 기록 → feed JSON `published_urls` 갱신"]
    if caveats:
        lines += ["", "## 7) 올리기 전 체크(글감 caveats)", ""]
        lines += [f"- [ ] {c}" for c in caveats]
    if ali:
        lines += ["", "### 알리 대체 상품", "",
                  f"- {ali.get('product_name')} / {ali.get('price_krw')}원 / {ali.get('url')}",
                  f"- {ali.get('note', '')}"]
    lines += ["", "---", "", f"> {item.get('disclosure', '')}", ""]
    return "\n".join(lines)


def build_manifest(item: dict, dry_run: bool, out_dir: Path) -> dict:
    return {
        "id": item.get("id"),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "out_dir": str(out_dir),
        "product_name": item.get("product_name"),
        "price_krw": item.get("price_krw"),
        "discount_rate": item.get("discount_rate"),
        "coupang_short_url": item.get("coupang_short_url"),
        "ali_short_url": item.get("ali_short_url"),
        "published_urls": item.get("published_urls"),
        "captions_note": ("captions.srt는 영상 음성 전사가 아니라 hooks.threads 문구를 클립 길이에 "
                          "배분한 초안입니다. 게시 전 영상과 대조해 고치세요."),
        "files": ["manifest.json", "codex_prompt.md", "captions.srt", "shorts_package.md"],
    }


# --------------------------------------------------------------------------- codex 실행


def run_codex(prompt: str, out_dir: Path, timeout: int = 900) -> "tuple[bool, str]":
    cmd_env = (os.environ.get("CODEX_CMD") or "").strip()
    if cmd_env:
        cmd = shlex.split(cmd_env)
        if "{}" in cmd:
            cmd = [c.replace("{}", prompt) for c in cmd]
        else:
            cmd = cmd + [prompt]
    else:
        # codex CLI 기본형: codex exec "<프롬프트>"
        cmd = ["codex", "exec", "--skip-git-repo-check", "--color", "never", prompt]

    try:
        proc = subprocess.run(cmd, cwd=str(out_dir), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, ("codex CLI를 찾지 못했습니다 — `npm i -g @openai/codex` 후 `codex login`, "
                       "또는 CODEX_CMD 환경변수로 직접 지정하세요. 템플릿 초안은 그대로 남습니다.")
    except subprocess.TimeoutExpired:
        return False, f"codex 실행이 {timeout}초를 넘겨 중단했습니다."
    except OSError as exc:
        return False, f"codex 실행 오류 — {exc}"

    (out_dir / "codex_output.md").write_text(proc.stdout or "(출력 없음)\n", encoding="utf-8")
    if proc.stderr:
        (out_dir / "codex_stderr.log").write_text(proc.stderr, encoding="utf-8")
    ok = proc.returncode == 0
    return ok, f"codex exit {proc.returncode}"


# --------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="새 글감 → Codex 작업물(output/{id}/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="git pull·상태저장·codex 실행 없이 초안 파일만 생성")
    ap.add_argument("--run-codex", action="store_true", help="로컬 codex CLI까지 호출")
    ap.add_argument("--no-pull", action="store_true", help="git pull 생략")
    ap.add_argument("--id", action="append", default=[], help="특정 글감 id만 처리(여러 번 가능)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="한 번에 처리할 최대 건수")
    ap.add_argument("--state", default=str(STATE_PATH))
    ap.add_argument("--out", default=str(OUT_ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    common.load_dotenv()
    in_launchd = bool(os.environ.get("CODEX_SYNC_DRY_RUN"))
    if in_launchd and (os.environ.get("CODEX_SYNC_DRY_RUN") or "").strip() in ("1", "true", "yes"):
        args.dry_run = True
    log = common.setup_logging(args.verbose)

    state_path = Path(args.state)
    out_root = Path(args.out)
    state = read_json(state_path, default={}) or {}
    processed = state.get("ids", {}) if isinstance(state, dict) else {}
    failed = state.get("failed", {}) if isinstance(state, dict) else {}
    max_codex_attempts = 3

    if args.dry_run:
        log.info("미리보기 모드 — pull·상태저장·codex 실행 안 함")
    else:
        pull_repo(log, enabled=not args.no_pull)

    items = sort_by_created(load_feed(log))
    if not items:
        log.warning("feed에 글감이 없습니다 — %s", FEED_DIR)
        return 0

    targets = [it for it in items if it.get("id") not in processed]
    if args.id:
        wanted = set(args.id)
        targets = [it for it in items if it.get("id") in wanted and it.get("id") not in processed]
    targets = targets[: max(0, args.limit)]

    if not targets:
        log.info("새 글감 없음 (전체 %d건, 처리 완료 %d건)", len(items), len(processed))
        if args.json:
            print(json.dumps({"new": 0, "total": len(items), "processed": len(processed)},
                             ensure_ascii=False, indent=2))
        return 0

    results = []
    for item in targets:
        item_id = item.get("id") or "unknown"
        out_dir = out_root / item_id
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            (out_dir / "manifest.json").write_text(
                json.dumps(build_manifest(item, args.dry_run, out_dir), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            prompt = build_prompt(item, out_dir)
            (out_dir / "codex_prompt.md").write_text(prompt, encoding="utf-8")
            (out_dir / "captions.srt").write_text(build_srt(item), encoding="utf-8")
            (out_dir / "shorts_package.md").write_text(build_package(item), encoding="utf-8")

            codex_ok, codex_msg = None, "미실행"
            if args.run_codex and not args.dry_run:
                codex_ok, codex_msg = run_codex(prompt, out_dir)
                log.info("codex %s — %s", "성공" if codex_ok else "실패", codex_msg)

            results.append({"id": item_id, "out": str(out_dir), "codex": codex_msg,
                            "codex_ok": codex_ok, "status": "ok"})
            log.info("생성 완료 %s → %s", item_id, out_dir.relative_to(BOT_DIR))

            retrying = False
            if not args.dry_run:
                entry = {
                    "processed_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "out": str(out_dir.relative_to(BOT_DIR)),
                    "codex": codex_msg,
                }
                if args.run_codex and codex_ok is False:
                    # codex 호출만 실패 → 템플릿은 남기고 다음 실행에서 재시도(최대 3회)
                    attempts = int((failed.get(item_id) or {}).get("attempts", 0)) + 1
                    if attempts < max_codex_attempts:
                        failed[item_id] = {"attempts": attempts, "last_error": codex_msg,
                                           "at": entry["processed_at"]}
                        log.warning("codex 재시도 예정 %s (%d/%d회)", item_id, attempts, max_codex_attempts)
                        retrying = True
                    else:
                        entry["codex"] = f"{codex_msg} — {max_codex_attempts}회 실패로 중단"
                        failed.pop(item_id, None)
                else:
                    failed.pop(item_id, None)
                if not retrying:
                    processed[item_id] = entry

            if retrying:
                results[-1]["status"] = "codex_retry"
                results[-1]["message"] = f"codex 실패 — 다음 실행에서 다시 시도({failed[item_id]['attempts']}/{max_codex_attempts})"
        except Exception as exc:  # 한 건 실패가 나머지를 막지 않게
            log.exception("생성 실패 %s — %s", item_id, exc)
            results.append({"id": item_id, "out": str(out_dir), "status": "error", "error": str(exc)})

    if not args.dry_run:
        write_json(state_path, {"ids": processed, "failed": failed,
                                "updated_at": datetime.now(KST).isoformat(timespec="seconds")})

    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "new": len(results), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{'[미리보기] ' if args.dry_run else ''}새 글감 {len(results)}건 처리")
        for r in results:
            print(f"- {r['id']}: {r['status']} (codex {r.get('codex')}) → {r['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
