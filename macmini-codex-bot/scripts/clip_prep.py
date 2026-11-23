#!/usr/bin/env python3
"""클립 준비 — 세로 숏폼 규격(720x1280)으로 다듬고, 게시 전 눈으로 볼 프레임을 뽑는다.

맥미니(Codex)가 발사대 숏폼을 만들 때 쓰는 도구입니다. 무거운 인코딩은 맥미니가 하고,
유리는 완성된 클립·프레임·카피 초안만 확인하면 됩니다.

하는 일
  1) `--src` 가 URL이면 yt-dlp로 원본을 받는다(`video.fetch_command` 대신 써도 됨)
  2) `--start`/`--duration` 구간을 잘라 720x1280(9:16)로 크롭·스케일 → `{id}_clip_small.mp4`
  3) 0초 / 0.5초 / 1.5초 프레임을 JPG로 뽑는다(첫 장면이 검은 화면인지 확인용)
  4) 결과 규격(가로·세로·길이)을 검사하고 `clip_manifest.json`에 남긴다

사용법
    python3 scripts/clip_prep.py --check
    python3 scripts/clip_prep.py --src "https://www.tiktok.com/@x/video/123" --id 20261123_0800
    python3 scripts/clip_prep.py --src ~/Downloads/a.mp4 --id test --start 2 --duration 20
    python3 scripts/clip_prep.py --src a.mp4 --id test --plan      # 실행 없이 명령만 확인

영상 파일은 저장소에 커밋하지 않습니다(`output/`은 .gitignore 대상). 프레임·매니페스트만 공유하려면
`drafts/{id}/clips/` 로 복사하세요(영상 본체 제외).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import BOT_DIR, have, run  # noqa: E402

KST = timezone(timedelta(hours=9))
TARGET_W, TARGET_H = 720, 1280
FRAME_TIMES = (0.0, 0.5, 1.5)
DEFAULT_DURATION = 20.0


def ffmpeg_cmd(src: Path, out: Path, start: float, duration: float) -> "list[str]":
    vf = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
          f"crop={TARGET_W}:{TARGET_H},setsar=1,fps=30")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{duration:.2f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", str(out)]


def frame_cmd(video: Path, at: float, out: Path) -> "list[str]":
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)]


def probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    proc = run(cmd, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {(proc.stderr or '').strip()[:200]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "duration_sec": round(float(data.get("format", {}).get("duration") or 0), 2),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": video.get("codec_name"),
        "fps": video.get("r_frame_rate"),
        "audio": bool(audio),
        "size_mb": round(int(data.get("format", {}).get("size") or 0) / 1024 ** 2, 2),
    }


def download(url: str, dest: Path, log) -> None:
    if not have("yt-dlp"):
        raise RuntimeError("yt-dlp가 없습니다 → `brew install yt-dlp` (또는 --src에 로컬 파일 경로)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "--no-part", "--force-overwrites",
           "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
           "--merge-output-format", "mp4", "-o", str(dest), url]
    log.info("원본 내려받기 — %s", url)
    proc = run(cmd, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp 실패: {(proc.stderr or '').strip()[-300:]}")
    if not dest.exists():
        # yt-dlp가 확장자를 바꿔 저장한 경우
        alt = list(dest.parent.glob(dest.stem + ".*"))
        if alt:
            alt[0].replace(dest)
        else:
            raise RuntimeError("yt-dlp가 파일을 만들지 못했습니다")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="클립 720x1280 정규화 + 프레임 검사")
    ap.add_argument("--src", help="원본 URL 또는 로컬 영상 경로")
    ap.add_argument("--id", help="글감 id (파일명 앞부분)")
    ap.add_argument("--start", type=float, default=0.0, help="자르기 시작(초, 기본 0)")
    ap.add_argument("--duration", type=float, default=None, help=f"길이(초, 기본 {DEFAULT_DURATION:.0f})")
    ap.add_argument("--out", default=None, help="출력 폴더(기본 output/{id}/clips)")
    ap.add_argument("--check", action="store_true", help="도구(ffmpeg/ffprobe/yt-dlp)만 확인")
    ap.add_argument("--plan", action="store_true", help="실행 없이 ffmpeg 명령만 출력")
    ap.add_argument("--keep-source", action="store_true", help="내려받은 원본을 남긴다")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    common.load_dotenv()
    log = common.setup_logging(args.verbose)

    missing = [n for n in ("ffmpeg", "ffprobe") if not have(n)]
    tools = {n: bool(have(n)) for n in ("ffmpeg", "ffprobe", "yt-dlp")}
    if args.check or (not args.src):
        print("ffmpeg:", "있음" if tools["ffmpeg"] else "없음 → brew install ffmpeg")
        print("ffprobe:", "있음" if tools["ffprobe"] else "없음 → brew install ffmpeg")
        print("yt-dlp:", "있음" if tools["yt-dlp"] else "없음 → brew install yt-dlp (URL 원본 받을 때만 필요)")
        return 0 if not missing else 2
    if missing and not args.plan:
        print(f"[중단] {', '.join(missing)} 가 없습니다 → `brew install ffmpeg`", file=sys.stderr)
        return 2
    if not args.id:
        print("[중단] --id 가 필요합니다 (예: --id 20261123_0800)", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else BOT_DIR / "output" / args.id / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(args.src).expanduser()
    is_url = str(args.src).startswith(("http://", "https://"))
    downloaded = None
    if is_url:
        downloaded = out_dir / f"{args.id}_source.mp4"
        if not args.plan:
            download(args.src, downloaded, log)
        src = downloaded
    elif not src.exists():
        print(f"[중단] 원본 파일이 없습니다: {src}", file=sys.stderr)
        return 2
    elif not args.plan:
        downloaded = out_dir / f"{args.id}_source{suffix_of(src)}"
        shutil.copy2(src, downloaded)
        src = downloaded

    duration = args.duration if args.duration else DEFAULT_DURATION
    out = out_dir / f"{args.id}_clip_small.mp4"

    plan = {
        "source": str(args.src),
        "start": args.start,
        "duration": duration,
        "output": str(out),
        "ffmpeg": " ".join(ffmpeg_cmd(src, out, args.start, duration)),
        "frames": [f"{t:.1f}s → frame_{t:.1f}.jpg" for t in FRAME_TIMES],
    }
    if args.plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json
              else "\n".join([f"원본: {plan['source']}", f"출력: {plan['output']}",
                              f"구간: {args.start:.2f} ~ {args.start + duration:.2f}초", "",
                              "ffmpeg:", plan["ffmpeg"], "", "프레임: " + ", ".join(plan["frames"])]))
        return 0

    try:
        proc = run(ffmpeg_cmd(src, out, args.start, duration), timeout=1800)
    except RuntimeError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    if proc.returncode != 0 or not out.exists():
        print(f"[실패] ffmpeg: {(proc.stderr or '').strip()[-400:]}", file=sys.stderr)
        return 1

    info = probe(out)
    frames = []
    for t in FRAME_TIMES:
        if t > info["duration_sec"]:
            continue
        frame = out_dir / f"frame_{t:.1f}.jpg"
        fp = run(frame_cmd(out, t, frame), timeout=120)
        frames.append({"at": t, "file": frame.name, "ok": frame.exists() and fp.returncode == 0})

    checks = {
        "720x1280": info["width"] == TARGET_W and info["height"] == TARGET_H,
        "duration": abs(info["duration_sec"] - duration) <= 0.35,
        "frames": all(f["ok"] for f in frames) and bool(frames),
        "audio": info["audio"],
    }
    manifest = {
        "id": args.id,
        "made_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": str(args.src),
        "start": args.start,
        "duration": duration,
        "output": out.name,
        "probe": info,
        "frames": frames,
        "checks": checks,
        "ok": checks["720x1280"] and checks["duration"] and checks["frames"],
        "note": "프레임 0.0/0.5/1.5초는 첫 장면이 검은 화면·자막 겹침인지 눈으로 확인하는 용도입니다.",
    }
    (out_dir / "clip_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.keep_source and downloaded and downloaded.exists():
        downloaded.unlink(missing_ok=True)

    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"클립 완성 — {out}")
        print(f"  규격 {info['width']}x{info['height']} · {info['duration_sec']}초 · {info['size_mb']}MB · "
              f"오디오 {'있음' if info['audio'] else '없음'}")
        print(f"  프레임: {', '.join(f['file'] for f in frames) or '없음'}")
        bad = [k for k, v in checks.items() if not v and k != 'audio']
        print(f"  검사: {'모두 통과' if not bad else '확인 필요 — ' + ', '.join(bad)}")
        print(f"  매니페스트: {out_dir / 'clip_manifest.json'}")
    return 0 if manifest["ok"] else 1


def suffix_of(path: Path) -> str:
    return path.suffix or ".mp4"


if __name__ == "__main__":
    raise SystemExit(main())
