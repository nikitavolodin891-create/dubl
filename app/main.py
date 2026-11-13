"""Дубль — сервер. Принимает ник Instagram, запускает разбор в фоне, отдаёт результат."""
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import brain, demo, pipeline

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dubl")

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dubl.sqlite3"

# Защита бюджета: сколько платных разборов можно запустить за сутки
MAX_PER_DAY = int(os.environ.get("MAX_ANALYSES_PER_DAY", "20"))
# Сколько дней повторный запрос по тому же нику отдаётся из сохранённого (бесплатно)
CACHE_DAYS = int(os.environ.get("CACHE_DAYS", "7"))
# Сколько сообщений в чате можно отправить по одному аккаунту за сутки
CHAT_PER_DAY = int(os.environ.get("CHAT_PER_DAY", "40"))

app = FastAPI(title="Дубль")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analyses (username TEXT PRIMARY KEY, created_at REAL, result TEXT)"
    )
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


def cached_result(username: str):
    with db() as conn:
        row = conn.execute(
            "SELECT created_at, result FROM analyses WHERE username = ?", (username,)
        ).fetchone()
    if row and time.time() - row[0] < CACHE_DAYS * 86400:
        return json.loads(row[1])
    return None


def runs_last_day() -> int:
    with db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM runs WHERE started_at > ?", (time.time() - 86400,)
        ).fetchone()[0]


def worker(job_id: str, username: str) -> None:
    job = jobs[job_id]

    def report(step: str, progress: int) -> None:
        job["step"], job["progress"] = step, progress

    try:
        result = pipeline.run(username, report)
        if not result.get("demo"):
            with db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO analyses VALUES (?, ?, ?)",
                    (username, time.time(), json.dumps(result, ensure_ascii=False)),
                )
        job.update(status="done", progress=100, result=result)
    except pipeline.UserError as e:
        job.update(status="error", error=str(e))
    except Exception:
        log.exception("Разбор @%s упал", username)
        job.update(status="error", error="Что-то сломалось на нашей стороне. Попробуй ещё раз через минуту.")


class AnalyzeIn(BaseModel):
    username: str


@app.post("/api/analyze")
def analyze(body: AnalyzeIn):
    username = clean_username(body.username)

    with jobs_lock:
        # чистим старые задачи и не запускаем второй разбор того же ника
        now = time.time()
        for jid in [j for j, v in jobs.items() if now - v["created"] > 3600]:
            del jobs[jid]
        for jid, v in jobs.items():
            if v["username"] == username and v["status"] == "running":
                return {"job_id": jid}

        job_id = uuid.uuid4().hex
        job = {"username": username, "status": "running", "step": "collect",
               "progress": 0, "created": now, "result": None, "error": None}

        cached = None if pipeline.demo_mode() else cached_result(username)
        if cached:
            job.update(status="done", progress=100, result=cached)
            jobs[job_id] = job
            return {"job_id": job_id}

        if not pipeline.demo_mode():
            if runs_last_day() >= MAX_PER_DAY:
                raise HTTPException(429, "На сегодня лимит разборов исчерпан. Загляни завтра.")
            with db() as conn:
                conn.execute("INSERT INTO runs VALUES (?, ?)", (username, now))

        jobs[job_id] = job

    threading.Thread(target=worker, args=(job_id, username), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Разбор не найден. Запусти заново.")
    return {k: job[k] for k in ("status", "step", "progress", "result", "error")}


class ChatIn(BaseModel):
    username: str
    message: str
    history: list[dict] = []


@app.post("/api/chat")
def chat(body: ChatIn):
    """Вопрос в чате. Ответ — короткая реплика и карточки, которые появятся на экране."""
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

    with db() as conn:
        row = conn.execute("SELECT result FROM analyses WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(404, "Сначала запусти разбор этого аккаунта.")
        sent = conn.execute(
            "SELECT COUNT(*) FROM chats WHERE username = ? AND sent_at > ?",
            (username, time.time() - 86400),
        ).fetchone()[0]
        if sent >= CHAT_PER_DAY:
            raise HTTPException(429, "На сегодня вопросы по этому аккаунту закончились. Продолжим завтра.")
        conn.execute("INSERT INTO chats VALUES (?, ?)", (username, time.time()))

    try:
        return brain.chat(json.loads(row[0]), message, history)
    except Exception:
        log.exception("Чат по @%s упал", username)
        raise HTTPException(502, "Не получилось ответить. Отправь вопрос ещё раз.")


@app.get("/api/config")
def config():
    return {"demo": pipeline.demo_mode()}


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")
