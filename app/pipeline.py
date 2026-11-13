"""Весь разбор аккаунта: сбор рилсов -> цифры -> расшифровка речи -> анализ -> сценарии."""
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import brain, demo, instagram, speech

TRANSCRIBE_LIMIT = int(os.environ.get("TRANSCRIBE_LIMIT", "12"))
REELS_LIMIT = int(os.environ.get("REELS_LIMIT", "30"))


class UserError(Exception):
    """Ошибка, текст которой можно показать человеку (закрытый аккаунт, нет рилсов и т.п.)."""


def demo_mode() -> bool:
    """Без ключей сайт работает на выдуманных данных и ничего не тратит.
    Нужны: Apify + (Claude или Gemini) + (OpenAI или Groq)."""
    if os.environ.get("DEMO") == "1":
        return True
    env = os.environ.get
    ready = env("APIFY_TOKEN") and (env("ANTHROPIC_API_KEY") or env("GEMINI_API_KEY")) and (env("OPENAI_API_KEY") or env("GROQ_API_KEY"))
    return not ready


def run(username: str, report) -> dict:
    if demo_mode():
        return demo.run(username, report)

    report("collect", 5)
    try:
        reels = instagram.fetch_reels(username, REELS_LIMIT)
    except instagram.NotAvailable as e:
        raise UserError(str(e)) from e
    if len(reels) < 3:
        raise UserError(
            "Не нашли у этого аккаунта открытых рилсов. Проверь ник и что профиль не закрыт."
        )

    report("numbers", 25)
    stats = compute_stats(reels)

    report("speech", 35)
    targets = pick_for_transcription(reels)
    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(lambda r: speech.transcribe(r["video_url"]), targets))
    for reel, text in zip(targets, texts):
        reel["transcript"] = text
    paces = [
        len(r["transcript"].split()) / r["duration"] * 60
        for r in targets
        if r.get("transcript") and r["duration"] > 3
    ]
    stats["words_per_minute"] = round(statistics.median(paces)) if paces else None
    stats["transcribed"] = sum(1 for r in targets if r.get("transcript"))

    report("analysis", 60)
    analysis = brain.analyze(username, reels, stats)

    report("scripts", 80)
    scripts = brain.write_scripts(username, reels, stats, analysis)

    return {
        "username": username,
        "demo": False,
        "generated_at": time.time(),
        "stats": stats,
        "reels": [public_reel(r) for r in reels],
        "analysis": analysis,
        "scripts": scripts,
    }


def compute_stats(reels: list[dict]) -> dict:
    """Цифры считает код, а не ИИ, чтобы в них не было выдумок."""
    views = [r["views"] for r in reels]
    median = statistics.median(views) or 1
    for r in reels:
        r["ratio"] = round(r["views"] / median, 2)
    rates = [(r["likes"] + r["comments"]) / r["views"] for r in reels if r["views"] > 0]

    dates = sorted(r["ts"] for r in reels if r["ts"])
    per_week = None
    if len(dates) >= 2 and dates[-1] > dates[0]:
        per_week = round((len(dates) - 1) / ((dates[-1] - dates[0]) / 86400 / 7), 1)

    return {
        "reels_count": len(reels),
        "median_views": round(median),
        "max_views": max(views),
        "engagement_pct": round(statistics.median(rates) * 100, 1) if rates else None,
        "reels_per_week": per_week,
    }


def pick_for_transcription(reels: list[dict]) -> list[dict]:
    """Берём самые сильные и самые слабые ролики: на контрасте видно, что работает."""
    with_video = [r for r in reels if r.get("video_url")]
    half = TRANSCRIBE_LIMIT // 2
    best = sorted(with_video, key=lambda r: -r["views"])[:half]
    # ролики младше 3 дней ещё набирают просмотры, в «слабые» их не записываем
    settled = [r for r in with_video if time.time() - r["ts"] > 3 * 86400 and r not in best]
    worst = sorted(settled, key=lambda r: r["views"])[:half]
    return best + worst


def public_reel(r: dict) -> dict:
    return {
        "n": r["n"],
        "url": r["url"],
        "views": r["views"],
        "likes": r["likes"],
        "comments": r["comments"],
        "duration": r["duration"],
        "ratio": r["ratio"],
        "date": datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%d.%m") if r["ts"] else "",
        "hook": " ".join((r.get("transcript") or r["caption"]).split()[:12]),
    }
