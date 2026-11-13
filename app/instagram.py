"""Публичные данные Instagram через Apify (тот же сервис, что у бота)."""
import os
from datetime import datetime

import httpx

# Какой «актор» Apify собирает рилсы. Можно поменять в переменных окружения.
ACTOR = os.environ.get("APIFY_ACTOR", "apify~instagram-reel-scraper")


class NotAvailable(Exception):
    pass


def fetch_reels(username: str, limit: int) -> list[dict]:
    r = httpx.post(
        f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
        headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}"},
        json={"username": [username], "resultsLimit": limit},
        timeout=320,
    )
    r.raise_for_status()
    items = r.json()
    if not isinstance(items, list):
        raise RuntimeError("Apify вернул неожиданный ответ")

    reels = []
    for it in items:
        if not isinstance(it, dict) or it.get("error"):
            continue
        views = it.get("videoPlayCount") or it.get("videoViewCount") or 0
        if not views:
            continue
        reels.append(
            {
                "url": it.get("url") or "",
                "views": int(views),
                "likes": max(0, int(it.get("likesCount") or 0)),  # -1, если лайки скрыты
                "comments": max(0, int(it.get("commentsCount") or 0)),
                "duration": float(it.get("videoDuration") or 0),
                "caption": (it.get("caption") or "").strip(),
                "video_url": it.get("videoUrl") or "",
                "ts": _timestamp(it.get("timestamp")),
            }
        )

    if not reels and any(isinstance(it, dict) and it.get("error") for it in items):
        raise NotAvailable(
            "Instagram не отдал данные по этому нику. Проверь написание и что профиль открыт."
        )

    reels.sort(key=lambda x: -x["ts"])
    for i, reel in enumerate(reels, 1):
        reel["n"] = i  # номер ролика: 1 — самый свежий
    return reels


def _timestamp(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
