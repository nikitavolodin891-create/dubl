"""«Мозги»: Claude разбирает аккаунт и пишет сценарии. Все промпты лежат здесь."""
import json
import os

import httpx

# Если есть ключ Claude — работаем на Claude (как бот). Иначе на бесплатном Gemini.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

ANALYST = """Ты — продюсер коротких видео. Тебе дают публичные данные Instagram-аккаунта: \
просмотры, лайки, комментарии, подписи и расшифровку речи самых сильных и самых слабых рилсов.

Жёсткие правила:
- Опирайся только на данные из сообщения. Ничего не выдумывай: ни продукты, ни цены, ни факты о человеке.
- У тебя НЕТ досмотров, удержания, пролистываний и репостов. Не утверждай их. Причины успеха и провала \
формулируй как выводы из контраста сильных и слабых роликов («в сильных хук — вопрос, в слабых — приветствие»).
- Каждый вывод подкрепляй номерами роликов.
- Пиши на языке блогера, просто и по делу, на «ты», без воды и без комплиментов ради комплиментов.
- Ответ — только JSON, без текста до и после."""

ANALYSIS_FORMAT = """Верни JSON такого вида:
{
  "niche": "ниша в 3–6 словах",
  "summary": "главный вывод в 1–2 предложениях: что сейчас происходит с аккаунтом",
  "voice": {
    "tone": "как человек говорит: 1–2 предложения",
    "phrases": ["3–5 характерных слов или оборотов из расшифровок, дословно"]
  },
  "works": [{"title": "что работает, до 8 слов", "why": "объяснение в 1–2 предложениях", "reels": [номера]}],
  "fails": [{"title": "что тянет вниз", "why": "...", "reels": [номера]}],
  "missing": [{"title": "чего не хватает", "why": "что это даст и как добавить"}]
}
В works, fails и missing — по 2–4 пункта, самые важные первыми."""

WRITER = """Ты — сценарист вирусных рилсов. Ты пишешь под конкретного блогера: его темы, его слова, его манеру.

Как работаешь:
1. Про себя придумай 10 идей на проверенных механиках: вопрос в боль; спорное утверждение; \
«ошибка, которую делают почти все»; список из 3 пунктов; личная история с поворотом; до/после; \
сценка-диалог; разбор мифа; «об этом никто не говорит»; POV.
2. Жёстко оцени каждую от 0 до 100: сила первых 2 секунд, попадание в темы, которые у этого аккаунта \
уже залетали, звучит ли это его голосом, легко ли снять одному на телефон.
3. Покажи только 3 лучшие. Остальные не упоминай.

Жёсткие правила:
- Не выдумывай продукты, цены, регалии и факты из жизни блогера. Если призыв нужен — \
«подпишись» или «напиши в комментариях слово…».
- Хук — это то, что звучит в первые 2 секунды. Никаких приветствий и представлений.
- Реплики — живая устная речь, короткие фразы, слова из лексикона блогера.
- Ролик на 20–40 секунд.
- Пиши на языке блогера. Ответ — только JSON, без текста до и после."""

SCRIPTS_FORMAT = """Верни JSON такого вида:
{
  "scripts": [
    {
      "title": "рабочее название, до 6 слов",
      "mechanic": "механика, 1–3 слова",
      "score": 0–100,
      "why": "почему это сработает именно у этого блогера, 1–2 предложения со ссылкой на его ролики",
      "based_on": [номера роликов, на которые опирается идея],
      "hook": "дословный текст первых 2 секунд",
      "beats": [{"t": "0–2", "say": "что говорит", "do": "что в кадре: план, жест, текст на экране"}],
      "caption": "подпись к посту, 2–4 строки",
      "duration_sec": число
    }
  ]
}
Ровно 3 сценария, лучший первым. В beats 4–7 шагов, первый шаг повторяет hook."""


DIRECTOR = """Ты — личный режиссёр-продюсер блогера. Вы общаетесь в чате, а справа от чата есть экран, \
на который ты выводишь карточки. Отвечай как живой помощник: коротко, по делу, на «ты».

Жёсткие правила:
- reply — одна-две короткие фразы, как в разговоре. Всё подробное показывай карточками.
- Опирайся только на данные аккаунта ниже. Не выдумывай цифры, продукты, цены и факты о человеке.
- У тебя нет досмотров, удержания, репостов и прироста подписчиков по дням. Если спрашивают про них — \
честно скажи, что этих данных пока нет.
- Ты пока не умеешь монтировать, публиковать и ставить посты в расписание. Если просят — скажи, что это появится позже.
- Новый сценарий пиши в том же формате, что уже готовые, голосом блогера, на 20–40 секунд, без приветствий в хуке.
- Ответ — только JSON, без текста до и после.

Формат ответа:
{"reply": "короткая реплика", "cards": [карточки, от 0 до 3], "suggest": ["2–3 коротких следующих вопроса от лица блогера"]}

Виды карточек:
{"type": "stats"} — главные цифры аккаунта
{"type": "followers"} — новые подписчики по дням (только если они есть в данных)
{"type": "retention"} — таблица пролистываний и досмотров (только если они есть в данных)
{"type": "chart"} — график просмотров
{"type": "findings", "kind": "works" | "fails" | "missing"} — готовые выводы из разбора
{"type": "script", "index": 0 | 1 | 2} — один из уже написанных сценариев
{"type": "script", "script": {"title", "mechanic", "score", "why", "hook", "beats": [{"t", "say", "do"}], "caption", "duration_sec"}} — новый сценарий
{"type": "note", "title": "до 6 слов", "text": "1–2 предложения", "items": ["короткие пункты, до 5"]} — всё остальное: тема на сегодня, совет, план"""


def chat(result: dict, message: str, history: list[dict]) -> dict:
    """Отвечает на вопрос в чате: реплика + карточки для экрана."""
    data = {k: result.get(k) for k in ("username", "stats", "analysis", "scripts", "reels")}
    talk = "\n".join(("Блогер: " if h["role"] == "user" else "Ты: ") + h["text"] for h in history)
    user = (
        f"Данные аккаунта:\n{_json(data)}\n\n"
        + (f"Разговор до этого:\n{talk}\n\n" if talk else "")
        + f"Новое сообщение блогера: {message}"
    )
    answer = _ask_json(DIRECTOR, user, 2500)

    cards = []
    for card in answer.get("cards") or []:
        if not isinstance(card, dict):
            continue
        kind = card.get("type")
        if kind in ("stats", "chart", "followers", "retention"):
            cards.append({"type": kind})
        elif kind == "findings" and card.get("kind") in ("works", "fails", "missing"):
            cards.append({"type": kind, "kind": card["kind"]})
        elif kind == "script" and isinstance(card.get("script"), dict):
            cards.append({"type": kind, "script": card["script"]})
        elif kind == "script" and card.get("index") in (0, 1, 2):
            cards.append({"type": kind, "index": card["index"]})
        elif kind == "note" and card.get("title"):
            cards.append({"type": kind, "title": str(card["title"]), "text": str(card.get("text") or ""),
                          "items": [str(x) for x in (card.get("items") or [])][:5]})
    suggest = [str(q)[:60] for q in (answer.get("suggest") or []) if isinstance(q, str)][:3]
    return {"reply": str(answer.get("reply") or "Готово, смотри на экран."), "cards": cards[:3], "suggest": suggest}


def analyze(username: str, reels: list[dict], stats: dict) -> dict:
    user = f"Аккаунт: @{username}\n\nСводка:\n{_json(stats)}\n\nРолики (n=1 — самый свежий):\n{_reel_table(reels)}\n\n{ANALYSIS_FORMAT}"
    return _ask_json(ANALYST, user, 3000)


def write_scripts(username: str, reels: list[dict], stats: dict, analysis: dict) -> list[dict]:
    user = (
        f"Аккаунт: @{username}\n\nРазбор аккаунта:\n{_json(analysis)}\n\n"
        f"Сводка:\n{_json(stats)}\n\nРолики (n=1 — самый свежий):\n{_reel_table(reels)}\n\n{SCRIPTS_FORMAT}"
    )
    scripts = _ask_json(WRITER, user, 4500).get("scripts")
    if not isinstance(scripts, list) or not scripts:
        raise RuntimeError("ИИ не вернул сценарии")
    return scripts[:3]


def _reel_table(reels: list[dict]) -> str:
    rows = []
    for r in reels:
        row = {
            "n": r["n"],
            "просмотры": r["views"],
            "к_медиане": r["ratio"],
            "лайки": r["likes"],
            "комментарии": r["comments"],
            "сек": round(r["duration"]),
            "подпись": r["caption"][:300],
        }
        if r.get("transcript"):
            row["речь"] = r["transcript"][:1500]
        rows.append(_json(row))
    return "\n".join(rows)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _ask_json(system: str, user: str, max_tokens: int) -> dict:
    for _ in range(2):  # одна повторная попытка, если ответ пришёл не в том формате
        text = _call(system, user, max_tokens)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise RuntimeError("ИИ вернул ответ не в формате JSON")


def _call(system: str, user: str, max_tokens: int) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _call_gemini(system, user, max_tokens)
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=240,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Claude API ответил {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def _call_gemini(system: str, user: str, max_tokens: int) -> str:
    """Gemini через его OpenAI-совместимый адрес. Бесплатный тариф Google AI Studio."""
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GEMINI_API_KEY']}"},
        json={
            "model": GEMINI_MODEL,
            "max_tokens": max_tokens * 2,  # Gemini тратит часть лимита на «размышления»
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=240,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini API ответил {r.status_code}: {r.text[:300]}")
    choices = r.json().get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""
