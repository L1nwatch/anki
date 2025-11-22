#!/usr/bin/env python3
"""Quick helper to add a few CLB9 word notes via AnkiConnect."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from import_clb9 import (  # type: ignore
    AnkiError,
    BASE_CSS,
    DECK_NAME,
    WORD_BACK,
    WORD_FRONT,
    WORD_MODEL,
    WORD_TEMPLATE,
    ensure_deck,
    ensure_model,
    ensure_word_audio,
    invoke,
    sanitize_word_for_audio,
)

DEFAULT_PROMPT = "请听写并拼写以下单词："
DEFAULT_TAGS = ["CLB9", "Word", "ManualAdd"]

# 这里硬编码一组示例单词，可根据需要增删。
WORD_LIST = [
    "ceramics",
    "scarf",
    "interpretation",
    "ethnography",
    "entrepreneurs",
    "respondents"
]


def find_existing_word_notes(word: str) -> list[int]:
    """Return Anki note ids that already contain this word in the `Word` field."""
    escaped_word = word.replace('"', '\\"')
    query = f'deck:"{DECK_NAME}" Word:"{escaped_word}"'
    result = invoke("findNotes", query=query)
    if not isinstance(result, list):  # should not happen, defensive
        return []
    return [int(note_id) for note_id in result]


def build_word_note_payload(word: str, audio_file: str | None) -> dict:
    db_id = f"manual-word-{sanitize_word_for_audio(word)}"
    fields = {
        "Prompt": DEFAULT_PROMPT,
        "Word": word,
        "WordAudio": f"[sound:{audio_file}]" if audio_file else "",
        "Stats": "",
        "DbId": db_id,
    }
    payload = {
        "deckName": DECK_NAME,
        "modelName": WORD_MODEL,
        "fields": fields,
        "tags": DEFAULT_TAGS,
        "options": {
            "allowDuplicate": True,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {"deckName": DECK_NAME, "checkChildren": False},
        },
        "guid": f"clb9-word-manual-{db_id}",
    }
    return payload


def ensure_word_notes(words: Iterable[str]) -> tuple[int, int]:
    """Ensure every word in the iterable exists as a CLB9 word note.

    Returns (created, skipped).
    """
    created = 0
    skipped = 0
    for word in words:
        cleaned = word.strip()
        if not cleaned:
            continue
        existing = find_existing_word_notes(cleaned)
        if existing:
            print(f"已存在：{cleaned}（跳过，找到 {len(existing)} 条笔记）")
            skipped += 1
            continue

        audio_file = ensure_word_audio(cleaned)
        payload = build_word_note_payload(cleaned, audio_file)
        invoke("addNotes", notes=[payload])
        print(f"已新增：{cleaned}")
        created += 1
    return created, skipped


def prepare_word_model() -> None:
    ensure_deck(DECK_NAME)
    ensure_model(
        WORD_MODEL,
        fields=["Prompt", "Word", "WordAudio", "Stats", "DbId"],
        template_name=WORD_TEMPLATE,
        front=WORD_FRONT,
        back=WORD_BACK,
        css=BASE_CSS,
    )


def main() -> None:
    prepare_word_model()
    created, skipped = ensure_word_notes(WORD_LIST)
    print(f"完成：新增 {created} 条，跳过 {skipped} 条。")


if __name__ == "__main__":
    try:
        main()
    except AnkiError as exc:
        print(exc)
    except Exception as exc:  # 捕获其它异常方便排查
        print(f"意外错误：{exc}")
