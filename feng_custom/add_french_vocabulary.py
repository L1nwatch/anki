#!/usr/bin/env python3
"""Add French vocabulary notes into the French-NCLC7 Anki deck."""
from __future__ import annotations

import base64
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple

import requests

API_URL = "http://127.0.0.1:8765"
API_VERSION = 6
DECK_NAME = "French-NCLC7"
MODEL_NAME = "French Vocabulary"
TEMPLATE_NAME = "Vocabulary"
VOCAB_PATH = Path(
    "/Users/fenglin/Desktop/code/anki/feng_custom/data/listen_cache/french_vocabulary.txt"
)

ANKI_MEDIA_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Anki2"
    / "User 1"
    / "collection.media"
)
FRENCH_VOICES = ["Thomas", "Amelie", "Aurelie", "Claire", "Alice"]
DEFAULT_TAGS = ["French", "Vocabulary", "NCLC7"]

CSS = """
.card {
  font-family: "Noto Sans", "Helvetica Neue", Arial, sans-serif;
  font-size: 26px;
  line-height: 1.5;
  color: #1a1a1a;
  background-color: #f6f9ff;
  padding: 28px;
  text-align: left;
}
.french-word {
  font-size: 36px;
  font-weight: 700;
  color: #13294b;
  margin-bottom: 18px;
}
.french-audio {
  margin-top: 12px;
}
.translation {
  margin-top: 20px;
  padding: 16px;
  border-left: 4px solid #3a7bfd;
  background: rgba(58, 123, 253, 0.12);
  border-radius: 8px;
}
.translation-label {
  font-weight: 600;
  color: #29457a;
  margin-bottom: 6px;
}
.translation-content {
  font-size: 24px;
  color: #0f213a;
}
"""

FRONT_TEMPLATE = """
<div class="card">
  <div class="french-word">{{French}}</div>
  {{#Audio}}<div class="french-audio">{{Audio}}</div>{{/Audio}}
</div>
"""

BACK_TEMPLATE = """
<div class="card">
  <div class="french-word">{{French}}</div>
  {{#Audio}}<div class="french-audio">{{Audio}}</div>{{/Audio}}
  <div class="translation">
    <div class="translation-label">English</div>
    <div class="translation-content">{{English}}</div>
  </div>
  <div class="translation">
    <div class="translation-label">中文释义</div>
    <div class="translation-content">{{Chinese}}</div>
  </div>
</div>
"""


def invoke(action: str, **params):
    payload = {"action": action, "version": API_VERSION, "params": params}
    resp = requests.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")


def ensure_deck(deck: str) -> None:
    decks = invoke("deckNames") or []
    if deck not in decks:
        invoke("createDeck", deck=deck)


def ensure_model() -> None:
    fields = ["French", "Audio", "English", "Chinese"]
    models = set(invoke("modelNames") or [])
    if MODEL_NAME not in models:
        invoke(
            "createModel",
            modelName=MODEL_NAME,
            inOrderFields=fields,
            css=CSS,
            isCloze=False,
            cardTemplates=[
                {"Name": TEMPLATE_NAME, "Front": FRONT_TEMPLATE, "Back": BACK_TEMPLATE}
            ],
        )
        return

    existing_fields = invoke("modelFieldNames", modelName=MODEL_NAME) or []
    for field in fields:
        if field not in existing_fields:
            invoke("modelFieldAdd", modelName=MODEL_NAME, fieldName=field)

    invoke(
        "updateModelStyling",
        model={"name": MODEL_NAME, "css": CSS},
    )
    invoke(
        "updateModelTemplates",
        model={
            "name": MODEL_NAME,
            "templates": {
                TEMPLATE_NAME: {
                    "Front": FRONT_TEMPLATE,
                    "Back": BACK_TEMPLATE,
                }
            },
        },
    )


def sanitize_for_audio(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "french"


def ensure_audio(word: str) -> str:
    if not word:
        return ""

    filename = f"french_vocab_{sanitize_for_audio(word.lower())}.mp3"
    if ANKI_MEDIA_DIR.exists():
        existing = ANKI_MEDIA_DIR / filename
        if existing.exists() and existing.stat().st_size > 0:
            return filename

    try:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as aiff_handle:
            aiff_path = Path(aiff_handle.name)
        last_error: subprocess.CalledProcessError | None = None
        for voice in FRENCH_VOICES:
            say_cmd = ["say", "-v", voice, "-o", str(aiff_path), word]
            try:
                subprocess.run(
                    say_cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                break
            except subprocess.CalledProcessError as exc:
                last_error = exc
        else:
            if last_error is not None:
                stderr = last_error.stderr.decode("utf-8", errors="ignore")
                print(f"生成音频失败：{word} -> {stderr}")
                return ""

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_handle:
            mp3_path = Path(mp3_handle.name)
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(aiff_path),
            "-acodec",
            "libmp3lame",
            str(mp3_path),
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        with mp3_path.open("rb") as mp3_file:
            encoded = base64.b64encode(mp3_file.read()).decode("ascii")
        stored = invoke("storeMediaFile", filename=filename, data=encoded)
        if isinstance(stored, str) and stored:
            return stored
        return filename
    except FileNotFoundError:
        print("缺少 say 或 ffmpeg 命令，无法生成法语音频。")
        return ""


def translate(word: str, target_lang: str) -> str:
    params = {
        "client": "gtx",
        "sl": "fr",
        "tl": target_lang,
        "dt": "t",
        "q": word,
    }
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params=params,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(part[0] for part in data[0]) if data else ""
    except Exception:
        return ""


def find_existing_notes(word: str) -> List[int]:
    escaped = word.replace('"', '\\"')
    query = f'deck:"{DECK_NAME}" French:"{escaped}"'
    try:
        result = invoke("findNotes", query=query)
    except RuntimeError:
        return []
    if not isinstance(result, list):
        return []
    out: List[int] = []
    for note_id in result:
        try:
            out.append(int(note_id))
        except (TypeError, ValueError):
            continue
    return out


def load_vocabulary(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Vocabulary file not found: {path}")
    seen = set()
    words: List[str] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            word = raw.strip()
            if not word or word.startswith("#"):
                continue
            if word in seen:
                continue
            seen.add(word)
            words.append(word)
    random.shuffle(words)
    return words


def build_note_payload(word: str, english: str, chinese: str, audio_file: str) -> dict:
    fields = {
        "French": word,
        "Audio": f"[sound:{audio_file}]" if audio_file else "",
        "English": english,
        "Chinese": chinese,
    }
    guid = f"french-nclc7-{sanitize_for_audio(word.lower())}"
    return {
        "deckName": DECK_NAME,
        "modelName": MODEL_NAME,
        "fields": fields,
        "tags": DEFAULT_TAGS,
        "options": {
            "allowDuplicate": True,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {"deckName": DECK_NAME, "checkChildren": False},
        },
        "guid": guid,
    }


def ensure_notes(words: Iterable[str]) -> Tuple[int, int]:
    created = 0
    skipped = 0
    for word in words:
        existing = find_existing_notes(word)
        if existing:
            print(f"已存在：{word}（跳过 {len(existing)} 条）")
            skipped += 1
            continue

        english = translate(word, "en")
        chinese = translate(word, "zh-CN")
        audio = ensure_audio(word)
        payload = build_note_payload(word, english, chinese, audio)
        invoke("addNotes", notes=[payload])
        print(f"已新增：{word}")
        created += 1
    return created, skipped


def main() -> None:
    ensure_deck(DECK_NAME)
    ensure_model()
    words = load_vocabulary(VOCAB_PATH)
    created, skipped = ensure_notes(words)
    print(f"完成：新增 {created} 条，跳过 {skipped} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"错误：{exc}")
