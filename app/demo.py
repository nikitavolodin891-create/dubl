"""Демо-режим: работает, пока на сервере не заданы ключи. Ничего не тратит."""
import time


def run(username: str, report) -> dict:
    for step, pct in (("collect", 10), ("numbers", 30), ("speech", 45), ("analysis", 65), ("scripts", 85)):
        report(step, pct)
        time.sleep(0.5)
    views = [4200, 61800, 8900, 27400, 5100, 112000]
    median = sorted(views)[len(views) // 2]
    reels = [
        {"n": i, "url": "", "views": v, "likes": v // 20, "comments": v // 200, "duration": 25,
         "ratio": round(v / median, 2), "date": "", "hook": "Пример ролика"}
        for i, v in enumerate(views, 1)
    ]
    return {
        "username": username,
        "demo": True,
        "demo_note": "Сервер работает в демо-режиме: ключи ещё не добавлены, поэтому цифры выдуманные.",
        "generated_at": time.time(),
        "stats": {"reels_count": len(reels), "median_views": median, "max_views": max(views),
                  "engagement_pct": 5.5, "reels_per_week": 3.0, "words_per_minute": None, "transcribed": 0},
        "reels": reels,
        "analysis": {
            "niche": "пример",
            "summary": "Это пример разбора. Добавь ключи на сервере, и здесь появится настоящий разбор аккаунта.",
            "works": [{"title": "Хук про зрителя", "why": "Ролики, где первая фраза про зрителя, набирают больше.", "reels": [6, 2]}],
            "fails": [{"title": "Приветствие в начале", "why": "Ролики с приветствием набирают меньше обычного.", "reels": [1, 5]}],
            "missing": [{"title": "Призыв в конце", "why": "Добавь просьбу написать слово в комментариях."}],
        },
        "scripts": [{
            "title": "Пример сценария", "mechanic": "Вопрос в боль", "score": 80,
            "why": "Пример, чтобы посмотреть, как выглядит сценарий.", "based_on": [6],
            "hook": "Ты не ленивая. Ты просто устала всё тянуть сама.",
            "beats": [{"t": "0–2", "say": "Ты не ленивая. Ты просто устала всё тянуть сама.", "do": "Крупный план"}],
            "caption": "Узнала себя? Напиши «да» в комментариях.", "duration_sec": 20,
        }],
    }


def chat(message: str, history: list[dict]) -> dict:
    return {"reply": "Сервер работает в демо-режиме. Когда добавим ключи, я буду отвечать по-настоящему.",
            "cards": [], "suggest": []}
