"""Дубль — сервер. Работает на Vercel: каждый запрос короткий и ничего не помнит между вызовами.

Разбор идёт по шагам, и шаги по очереди запускает сама страница:
  1. /api/start     — собираем рилсы и считаем цифры
  2. /api/speech    — расшифровываем речь (по одному ролику за запрос, несколько параллельно)
  3. /api/analysis  — ИИ ищет, что работает и что нет
  4. /api/scripts   — ИИ пишет сценарии, сервер отдаёт готовый разбор
Чтобы чужие люди не гоняли наш ИИ через шаги 2–4 напрямую, шаг 1 выдаёт подписанный пропуск на 30 минут.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import brain, demo, pipeline, speech

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dubl")

BASE = Path(__file__).resolve().parent.parent
# На Vercel писать можно только в /tmp, и он у каждого экземпляра сервера свой и временный.
# Поэтому кэш и лимиты здесь — «по возможности», а не строгий учёт.
DEFAULT_DATA = "/tmp/dubl" if os.environ.get("VERCEL") else str(BASE / "data")
DATA_DIR = Path(os.environ.get("DATA_DIR", DEFAULT_DATA))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dubl.sqlite3"

MAX_PER_DAY = int(os.environ.get("MAX_ANALYSES_PER_DAY", "20"))
CACHE_DAYS = int(os.environ.get("CACHE_DAYS", "7"))
CHAT_PER_DAY = int(os.environ.get("CHAT_PER_DAY", "40"))
PASS_TTL = 30 * 60

app = FastAPI(title="Дубль")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS analyses (username TEXT PRIMARY KEY, created_at REAL, result TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (username TEXT, started_at REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS chats (username TEXT, sent_at REAL)")
    return conn


def clean_username(raw: str) -> str:
    """Принимает '@nick', 'nick' или ссылку на профиль и возвращает чистый ник."""
    s = raw.strip()
    m = re.search(r"instagram\.com/([^/?#\s]+)", s, re.I)
    if m:
        s = m.group(1)
    s = s.lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}", s):
        raise HTTPException(400, "Не похоже на ник Instagram. Пример: anyalakshmi")
    return s


# ---------- Пропуск между шагами ----------

def _secret() -> bytes:
    own = os.environ.get("SECRET_KEY")
    if own:
        return own.encode()
    keys = "|".join(os.environ.get(k, "") for k in ("APIFY_TOKEN", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
    return hashlib.sha256(("dubl|" + keys).encode()).digest()


def make_pass(username: str) -> str:
    exp = int(time.time()) + PASS_TTL
    sig = hmac.new(_secret(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_pass(username: str, token: str) -> None:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        raise HTTPException(403, "Разбор устарел. Запусти его заново.")
    good = hmac.new(_secret(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
    if exp < time.time() or not hmac.compare_digest(sig, good):
        raise HTTPException(403, "Разбор устарел. Запусти его заново.")


def clean_reels(raw) -> list[dict]:
    """Рилсы приходят обратно со страницы — берём только нужные поля и проверяем типы."""
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Нет данных о рилсах. Запусти разбор заново.")
    reels = []
    for r in raw[: pipeline.REELS_LIMIT]:
        if not isinstance(r, dict):
            continue
        try:
            reels.append({
                "n": int(r["n"]),
                "url": str(r.get("url") or "")[:300],
                "views": int(r["views"]),
                "likes": int(r.get("likes") or 0),
                "comments": int(r.get("comments") or 0),
                "duration": float(r.get("duration") or 0),
                "caption": str(r.get("caption") or "")[:2200],
                "ts": float(r.get("ts") or 0),
                "ratio": float(r.get("ratio") or 0),
                "transcript": str(r.get("transcript") or "")[:4000],
            })
        except (KeyError, TypeError, ValueError):
            continue
    if len(reels) < 3:
        raise HTTPException(400, "Нет данных о рилсах. Запусти разбор заново.")
    return reels


# ---------- Шаги разбора ----------

class StartIn(BaseModel):
    username: str


@app.post("/api/start")
def start(body: StartIn):
    username = clean_username(body.username)

    if pipeline.demo_mode():
        return {"result": demo.run(username, lambda *a: None)}

    with db() as conn:
        row = conn.execute("SELECT created_at, result FROM analyses WHERE username = ?", (username,)).fetchone()
        if row and time.time() - row[0] < CACHE_DAYS * 86400:
            return {"result": json.loads(row[1])}
        runs = conn.execute("SELECT COUNT(*) FROM runs WHERE started_at > ?", (time.time() - 86400,)).fetchone()[0]
        if runs >= MAX_PER_DAY:
            raise HTTPException(429, "На сегодня лимит разборов исчерпан. Загляни завтра.")
        conn.execute("INSERT INTO runs VALUES (?, ?)", (username, time.time()))

    try:
        data = pipeline.collect(username)
    except pipeline.UserError as e:
        raise HTTPException(400, str(e))
    except Exception:
        log.exception("Сбор рилсов @%s упал", username)
        raise HTTPException(502, "Instagram не ответил вовремя. Попробуй ещё раз через минуту.")
    return {"username": username, "pass": make_pass(username), **data}


class SpeechIn(BaseModel):
    username: str
    token: str
    video_url: str


@app.post("/api/speech")
def speech_step(body: SpeechIn):
    """Расшифровка одного ролика. Если не получилось — пустой текст, разбор не падает."""
    username = clean_username(body.username)
    check_pass(username, body.token)
    return {"text": speech.transcribe(body.video_url)}


class AnalysisIn(BaseModel):
    username: str
    token: str
    reels: list
    stats: dict


@app.post("/api/analysis")
def analysis_step(body: AnalysisIn):
    username = clean_username(body.username)
    check_pass(username, body.token)
    reels = clean_reels(body.reels)
    stats = dict(body.stats)
    pipeline.add_speech_stats(reels, stats)
    try:
        return {"analysis": brain.analyze(username, reels, stats), "stats": stats}
    except Exception:
        log.exception("Анализ @%s упал", username)
        raise HTTPException(502, "ИИ не ответил. Нажми «Разобрать» ещё раз.")


class ScriptsIn(AnalysisIn):
    analysis: dict


@app.post("/api/scripts")
def scripts_step(body: ScriptsIn):
    username = clean_username(body.username)
    check_pass(username, body.token)
    reels = clean_reels(body.reels)
    stats = dict(body.stats)
    try:
        scripts = brain.write_scripts(username, reels, stats, body.analysis)
    except Exception:
        log.exception("Сценарии @%s упали", username)
        raise HTTPException(502, "ИИ не ответил. Нажми «Разобрать» ещё раз.")
    result = pipeline.final_result(username, reels, stats, body.analysis, scripts)
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO analyses VALUES (?, ?, ?)",
                     (username, time.time(), json.dumps(result, ensure_ascii=False)))
    return {"result": result}


# ---------- Чат ----------

class ChatIn(BaseModel):
    username: str
    message: str
    history: list[dict] = []
    result: dict | None = None


@app.post("/api/chat")
def chat(body: ChatIn):
    """Вопрос в чате. Разбор присылает сама страница — сервер его не хранит."""
    username = clean_username(body.username)
    message = body.message.strip()[:500]
    if not message:
        raise HTTPException(400, "Напиши вопрос.")
    history = [
        {"role": h.get("role"), "text": str(h.get("text", ""))[:600]}
        for h in body.history[-8:]
        if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ]

    if pipeline.demo_mode():
        return demo.chat(message, history)

    result = body.result
    if not isinstance(result, dict) or str(result.get("username", "")).lower() != username:
        raise HTTPException(400, "Сначала запусти разбор этого аккаунта.")
    if len(json.dumps(result, ensure_ascii=False)) > 300_000:
        raise HTTPException(400, "Разбор слишком большой. Запусти его заново.")

    with db() as conn:
        sent = conn.execute("SELECT COUNT(*) FROM chats WHERE username = ? AND sent_at > ?",
                            (username, time.time() - 86400)).fetchone()[0]
        if sent >= CHAT_PER_DAY:
            raise HTTPException(429, "На сегодня вопросы по этому аккаунту закончились. Продолжим завтра.")
        conn.execute("INSERT INTO chats VALUES (?, ?)", (username, time.time()))

    try:
        return brain.chat(result, message, history)
    except Exception:
        log.exception("Чат по @%s упал", username)
        raise HTTPException(502, "Не получилось ответить. Отправь вопрос ещё раз.")


@app.get("/api/config")
def config():
    return {"demo": pipeline.demo_mode()}


@app.get("/")
def index():
    # На Vercel главную отдаёт CDN из папки public, а этот маршрут — для запуска на своём компьютере
    return FileResponse(BASE / "public" / "index.html")
