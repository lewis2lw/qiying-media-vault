#!/usr/bin/env python3
"""
一次性修复存量帖子封面：源站自 2026-10-23 起的帖子正文首图是站方推广海报，
而封面取的正是首图，导致首页封面大面积是同一张广告图。

对 media-data/posts.json 中 date_published >= --since（默认 2026-10-23）的帖子，
按 sync_source_posts.extract_media 的新规则补做一次"跳过首图"：
  - 图片 >= 2 张：去掉第一张；
  - 只有 1 图但有视频：去掉该图（多为推广图），封面改走视频 pic；
  - 只有 1 图且无视频：保留（避免帖子一张图都不剩）。
处理后重算 cover / image_count / media_count，并打 first_image_skipped 标记；
带标记的帖子（包括以后新采集的帖子）一律跳过，保证幂等、可重复运行。

用法：
  python3 repair_covers.py                 # 实际修复
  python3 repair_covers.py --dry-run       # 只统计不写文件
  python3 repair_covers.py --since 2026-10-23 --posts media-data/posts.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
IMG_CDN = "https://imgpublic.ycomesc.live"


def log(msg: str) -> None:
    print(msg, flush=True)


def repair_post(post: dict[str, Any]) -> bool:
    """对单帖执行跳首图 + 重算封面。返回是否真的去掉了首图。"""
    images: list[dict] = post.get("images") or []
    videos: list[dict] = post.get("videos") or []
    dropped = False
    if len(images) > 1 or (images and videos):
        images = images[1:]
        post["images"] = images
        dropped = True
    if images:
        cover = images[0].get("cover") or f"{IMG_CDN}{images[0]['path']}"
    elif videos:
        cover = videos[0].get("cover") or ""
    else:
        cover = ""
    post["cover"] = cover
    post["image_count"] = len(images)
    post["media_count"] = len(images) + len(videos)
    post["first_image_skipped"] = True
    return dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repair covers by skipping promo first image")
    ap.add_argument("--since", type=str, default="2026-10-23",
                    help="只处理 date_published >= 该日期（YYYY-MM-DD）的帖子")
    ap.add_argument("--posts", type=str, default=str(ROOT / "media-data" / "posts.json"),
                    help="posts.json 路径")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.posts)
    posts: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    log(f"posts total: {len(posts):,}")

    in_scope = 0
    dropped = 0
    kept_single = 0
    already = 0
    for post in posts:
        if post.get("first_image_skipped"):
            already += 1
            continue
        published = str(post.get("date_published") or "")[:10]
        if not published or published < args.since:
            continue
        in_scope += 1
        if repair_post(post):
            dropped += 1
        else:
            kept_single += 1

    log(
        f"since={args.since} in_scope={in_scope:,} dropped_first={dropped:,} "
        f"kept_single_image={kept_single:,} already_marked={already:,}"
    )
    if args.dry_run:
        log("[dry-run] 未写文件")
        return 0
    if in_scope:
        path.write_text(
            json.dumps(posts, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        log(f"posts.json written: {path}")
    else:
        log("无可处理帖子，未写文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
