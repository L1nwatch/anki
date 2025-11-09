#!/usr/bin/env python3
"""Add French vocabulary notes into the French-NCLC7 Anki deck."""
from __future__ import annotations

import argparse
import base64
import random
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

import requests

API_URL = "http://127.0.0.1:8765"
API_VERSION = 6
DECK_NAME = "French-NCLC7-yisen"
MODEL_NAME = "French Vocabulary"
TEMPLATE_NAME = "Vocabulary"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_TRANSLATE_URL = "https://api.mymemory.translated.net/get"
VOCAB_PATH = Path(
    "/Users/fenglin/Desktop/code/anki/feng_custom/data/listen_cache/french_vocabulary-20251109.txt"
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
TRANSLATE_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class VocabEntry:
    french: str
    english: str
    chinese: str

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


def _translate_mymemory(word: str, target_lang: str) -> str:
    params = {"q": word, "langpair": f"fr|{target_lang}"}
    resp = requests.get(
        MYMEMORY_TRANSLATE_URL,
        params=params,
        timeout=10,
        headers=TRANSLATE_HEADERS,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    result = (data.get("responseData") or {}).get("translatedText", "")
    if result:
        return result
    matches = data.get("matches") or []
    if matches:
        return matches[0].get("translation", "") or ""
    return ""


def _translate_google(word: str, target_lang: str) -> str:
    params = {
        "client": "gtx",
        "sl": "fr",
        "tl": target_lang,
        "dt": "t",
        "q": word,
    }
    resp = requests.get(
        GOOGLE_TRANSLATE_URL,
        params=params,
        timeout=10,
        headers=TRANSLATE_HEADERS,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(part[0] for part in data[0]) if data else ""


def translate(word: str, target_lang: str) -> str:
    text = word.strip()
    if not text:
        return ""
    for translator in (_translate_mymemory, _translate_google):
        try:
            result = translator(text, target_lang).strip()
            if result:
                return result
        except Exception:
            continue
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


def load_vocabulary(path: Path) -> List[VocabEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Vocabulary file not found: {path}")
    seen = set()
    entries: List[VocabEntry] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            french = ""
            english = ""
            chinese = ""
            if "=" in line:
                parts = [part.strip() for part in line.split("=", 2)]
                if parts:
                    french = parts[0]
                if len(parts) > 1:
                    english = parts[1]
                if len(parts) > 2:
                    chinese = parts[2]
            else:
                french = line
            if not french or french in seen:
                continue
            seen.add(french)
            entries.append(VocabEntry(french=french, english=english, chinese=chinese))
    random.shuffle(entries)
    return entries


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


def ensure_notes(entries: Iterable[VocabEntry]) -> Tuple[int, int]:
    created = 0
    skipped = 0
    for entry in entries:
        word = entry.french
        existing = find_existing_notes(word)
        if existing:
            print(f"已存在：{word}（跳过 {len(existing)} 条）")
            skipped += 1
            continue

        english = entry.english or translate(word, "en")
        chinese = entry.chinese or translate(word, "zh-CN")
        audio = ensure_audio(word)
        payload = build_note_payload(word, english, chinese, audio)
        invoke("addNotes", notes=[payload])
        print(f"已新增：{word}")
        created += 1
    return created, skipped


def main() -> None:
    global DECK_NAME
    parser = argparse.ArgumentParser(description="Add French vocabulary to Anki decks.")
    parser.add_argument(
        "--deck",
        default=DECK_NAME,
        help="Target deck name (default: %(default)s)",
    )
    parser.add_argument(
        "--vocab-path",
        default=str(VOCAB_PATH),
        help="Path to the vocabulary list (default: %(default)s)",
    )
    args = parser.parse_args()

    DECK_NAME = args.deck

    vocab_path = Path(args.vocab_path).expanduser()

    ensure_deck(DECK_NAME)
    ensure_model()
    entries = load_vocabulary(vocab_path)
    created, skipped = ensure_notes(entries)
    print(f"完成：新增 {created} 条，跳过 {skipped} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"错误：{exc}")
