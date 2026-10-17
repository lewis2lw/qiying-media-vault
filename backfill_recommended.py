#!/usr/bin/env python3
"""One-off backfill: fill `recommended` for posts scraped before the
gallery / 热门推荐 split, and clean gallery images that mixed in
recommendation thumbnails.

For each post lacking `recommended`, re-fetch its source page and
re-extract with the current logic (extract_media + extract_recommended).
Only media fields are updated; all meta (title, description, author,
categories, tags, dates) is preserved.

Safe to re-run: posts that already have `recommended` are skipped
unless --force is given. Progress is checkpointed to posts.json after
every batch, so a killed run resumes where it left off.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_source_posts as sync


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_posts(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_posts(path: Path, posts: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(posts, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def backfill_one(post: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str]:
    """Return (pid, updated_fields_or_None, status). None = keep old record."""
    pid = int(post["pid"])
    try:
        status, html = sync.fetch(f"{sync.BASE}/archives/{pid}/")
    except Exception as e:  # noqa: BLE001 - keep old record on any fetch error
        return pid, None, f"fetch_error {e}"
    if status == 0 or status >= 500:
        time.sleep(0.5)
        try:
            status, html = sync.fetch(f"{sync.BASE}/archives/{pid}/")
        except Exception as e:  # noqa: BLE001
            return pid, None, f"fetch_error {e}"
    if status == 404 or not html:
        return pid, None, f"http_{status}"
    if status != 200:
        return pid, None, f"http_{status}"
    try:
        images, videos = sync.extract_media(html)
        recommended = sync.extract_recommended(html)
    except Exception as e:  # noqa: BLE001
        return pid, None, f"extract_error {e}"
    cover = ""
    if images:
        cover = images[0].get("cover") or f"{sync.IMG_CDN}{images[0]['path']}"
    elif videos:
        cover = videos[0].get("cover") or ""
    else:
        cover = post.get("cover") or ""
    fields: dict[str, Any] = {
        "images": images,
        "videos": videos,
        "recommended": recommended,
        "image_count": len(images),
        "video_count": len(videos),
        "media_count": len(images) + len(videos),
        "cover": cover,
    }
    if not post.get("source_url"):
        fields["source_url"] = f"{sync.BASE}/archives/{pid}/"
    return pid, fields, "ok"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill recommended for old posts")
    ap.add_argument("--posts", default="media-data/posts.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--batch", type=int, default=200, help="checkpoint every N posts")
    ap.add_argument("--limit", type=int, default=0, help="max posts to process (0=all)")
    ap.add_argument("--force", action="store_true", help="redo even if recommended exists")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.posts)
    if not path.is_file():
        log(f"[error] missing {path}")
        return 2
    posts = load_posts(path)
    by_pid = {int(p["pid"]): i for i, p in enumerate(posts)}
    todo = [
        int(p["pid"])
        for p in posts
        if args.force or not p.get("recommended")
    ]
    if args.limit and args.limit > 0:
        todo = todo[: args.limit]
    log(f"total posts: {len(posts):,} · to backfill: {len(todo):,}")
    if args.dry_run:
        log("[dry-run] sample pids: " + ", ".join(str(p) for p in todo[:20]))
        return 0
    if not todo:
        log("nothing to backfill")
        return 0

    t0 = time.time()
    done = ok = fail = 0
    for start in range(0, len(todo), args.batch):
        batch = todo[start : start + args.batch]
        updates: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {
                ex.submit(backfill_one, posts[by_pid[pid]]): pid for pid in batch
            }
            for fut in as_completed(futs):
                pid = futs[fut]
                done += 1
                try:
                    _pid, fields, status = fut.result()
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    log(f"  ! {pid} exception {e}")
                    continue
                if fields is None:
                    fail += 1
                    log(f"  · {pid} skip ({status})")
                else:
                    updates[pid] = fields
                    ok += 1
        for pid, fields in updates.items():
            posts[by_pid[pid]].update(fields)
        save_posts(path, posts)
        elapsed = time.time() - t0
        log(
            f"[{done}/{len(todo)}] ok={ok} fail={fail} "
            f"elapsed={elapsed:.0f}s eta={(elapsed / max(done, 1) * (len(todo) - done)):.0f}s "
            f"· checkpoint saved"
        )
    log(f"done · ok={ok} fail={fail} elapsed={time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
