"""Расшифровка речи из рилса: скачиваем видео, достаём звук, отправляем в Groq (бесплатно) или OpenAI."""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("dubl.speech")

# Если есть ключ Groq — расшифровываем бесплатно у него, иначе у OpenAI
if os.environ.get("GROQ_API_KEY"):
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"
    KEY_NAME = "GROQ_API_KEY"
    MODEL = os.environ.get("TRANSCRIBE_MODEL", "whisper-large-v3-turbo")
else:
    URL = "https://api.openai.com/v1/audio/transcriptions"
    KEY_NAME = "OPENAI_API_KEY"
    MODEL = os.environ.get("TRANSCRIBE_MODEL", "whisper-1")
# Скачиваем видео только с серверов Instagram
ALLOWED_HOSTS = (".cdninstagram.com", ".fbcdn.net")
MAX_DOWNLOAD = 80 * 1024 * 1024
MAX_UPLOAD = 24 * 1024 * 1024  # у обоих сервисов ограничение 25 МБ на файл


def transcribe(video_url: str) -> str:
    """Возвращает текст речи или пустую строку, если не получилось (разбор при этом не падает)."""
    try:
        host = urlparse(video_url).hostname or ""
        if not video_url.startswith("https://") or not host.endswith(ALLOWED_HOSTS):
            return ""
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "reel.mp4"
            _download(video_url, video)
            audio = _extract_audio(video)
            if audio.stat().st_size > MAX_UPLOAD:
                return ""
            with audio.open("rb") as f:
                r = httpx.post(
                    URL,
                    headers={"Authorization": f"Bearer {os.environ[KEY_NAME]}"},
                    data={"model": MODEL, "response_format": "json"},
                    files={"file": (audio.name, f)},
                    timeout=180,
                )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
    except Exception as e:
        log.warning("Не удалось расшифровать ролик: %s", type(e).__name__)
        return ""


def _download(url: str, dest: Path) -> None:
    size = 0
    with httpx.stream("GET", url, timeout=120, follow_redirects=False) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError("видео слишком большое")
                f.write(chunk)


def _extract_audio(video: Path) -> Path:
    """Если на сервере есть ffmpeg, отправляем только звук — это быстрее и надёжнее."""
    if not shutil.which("ffmpeg"):
        return video
    audio = video.with_suffix(".mp3")
    done = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(audio)],
        capture_output=True,
        timeout=120,
    )
    return audio if done.returncode == 0 and audio.exists() else video
