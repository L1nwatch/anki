#!/usr/bin/env python3
"""Flask web front-end that proxies Anki's reviewer via AnkiConnect."""
from __future__ import annotations

import base64
import difflib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent

ANKI_CONNECT = "http://127.0.0.1:8765"
DECK_NAME = "English-CLB9"
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)?|[.,!?;:()\"“”‘’]")
SOUND_RE = re.compile(r"\[sound:(.+?)\]")
TRANSLATE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
}

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

CURRENT_CARD: Optional[Dict[str, Any]] = None


def invoke(action: str, **params: Any) -> Any:
    payload = {"action": action, "version": 6, "params": params}
    resp = requests.post(ANKI_CONNECT, json=payload, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return body.get("result")


def extract_sound(value: str | None) -> str | None:
    if not value:
        return None
    match = SOUND_RE.search(value)
    return match.group(1) if match else None


def media_to_data_url(filename: str | None) -> str | None:
    if not filename:
        return None
    try:
        data = invoke("retrieveMediaFile", filename=filename)
    except Exception:
        return None
    if not data:
        return None
    return f"data:audio/mpeg;base64,{data}"


def deck_counts() -> Dict[str, int]:
    names = invoke("deckNamesAndIds") or {}
    deck_id = names.get(DECK_NAME)
    if deck_id is None:
        return {"due": 0, "new": 0, "learning": 0}
    stats_map = invoke("getDeckStats", decks=[DECK_NAME]) or {}
    stats = stats_map.get(str(deck_id), {})
    return {
        "due": stats.get("review_count", 0),
        "new": stats.get("new_count", 0),
        "learning": stats.get("learn_count", 0),
    }


def tokenize(text: str | None) -> List[str]:
    if not text:
        return []
    return TOKEN_RE.findall(text)


def build_diff(expected: str | None, actual: str | None) -> Dict[str, Any]:
    exp_tokens = tokenize(expected)
    act_tokens = tokenize(actual)
    differ = difflib.Differ()
    diff = list(differ.compare([t.lower() for t in exp_tokens], [t.lower() for t in act_tokens]))
    expected_out: List[Dict[str, str]] = []
    actual_out: List[Dict[str, str]] = []
    counts = {"match": 0, "missing": 0, "extra": 0}
    for entry in diff:
        if entry.startswith("? "):
            continue
        op = entry[:2]
        tok = entry[2:]
        if op == "  ":
            counts["match"] += 1
            expected_out.append({"text": tok, "status": "match"})
            actual_out.append({"text": tok, "status": "match"})
        elif op == "- ":
            counts["missing"] += 1
            expected_out.append({"text": tok, "status": "missing"})
        elif op == "+ ":
            counts["extra"] += 1
            actual_out.append({"text": tok, "status": "extra"})
    return {"expected": expected_out, "actual": actual_out, "counts": counts}


def translate_text(text: str) -> str:
    if not text:
        return ""
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN",
        "dt": "t",
        "q": text,
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


def determine_type(fields: Dict[str, Dict[str, str]]) -> str:
    names = set(fields.keys())
    if "Transcript" in names:
        return "listening"
    if "Word" in names:
        return "word"
    if "Original" in names:
        return "writing"
    return "generic"


def ensure_review_ready() -> Dict[str, Any]:
    try:
        card = invoke("guiCurrentCard")
        if card:
            return card
    except RuntimeError:
        pass
    invoke("guiDeckReview", name=DECK_NAME)
    card = invoke("guiCurrentCard")
    if not card:
        raise RuntimeError("no cards available")
    try:
        invoke("guiShowQuestion")
    except RuntimeError:
        pass
    return card


@app.get("/")
def index():
    return render_template("index.html", deck_name=DECK_NAME)


@app.get("/api/next")
def api_next():
    global CURRENT_CARD
    try:
        card = ensure_review_ready()
    except RuntimeError:
        CURRENT_CARD = None
        return jsonify({"error": "no cards due"}), 404

    # stop any automatic audio playback from Anki
    try:
        invoke("stopAudio")
        time.sleep(0.2)
        invoke("stopAudio")
    except RuntimeError:
        pass

    fields = card.get("fields", {})
    note_type = determine_type(fields)

    if note_type == "listening":
        audio_file = extract_sound(fields.get("Audio", {}).get("value", ""))
        data = {
            "prompt": fields.get("Prompt", {}).get("value", ""),
            "transcript": fields.get("Transcript", {}).get("value", ""),
            "source": fields.get("Source", {}).get("value", ""),
            "audioUrl": media_to_data_url(audio_file),
        }
    elif note_type == "word":
        audio_file = extract_sound(fields.get("WordAudio", {}).get("value", ""))
        data = {
            "prompt": fields.get("Prompt", {}).get("value", ""),
            "word": fields.get("Word", {}).get("value", ""),
            "audioUrl": media_to_data_url(audio_file),
        }
    elif note_type == "writing":
        data = {
            "prompt": fields.get("Prompt", {}).get("value", ""),
            "original": fields.get("Original", {}).get("value", ""),
            "corrected": fields.get("Corrected", {}).get("value", ""),
        }
    else:
        data = {
            "prompt": card.get("question", ""),
            "answer": card.get("answer", ""),
        }

    buttons = card.get("buttons", []) or [1, 2, 3, 4]
    CURRENT_CARD = {
        "cardId": card.get("cardId"),
        "type": note_type,
        "buttons": buttons,
    }

    counts = deck_counts()

    return jsonify(
        {
            "cardId": card.get("cardId"),
            "type": note_type,
            "data": data,
            "questionHtml": card.get("question", ""),
            "answerHtml": card.get("answer", ""),
            "buttons": buttons,
            "counts": counts,
        }
    )


@app.post("/api/reveal")
def api_reveal():
    try:
        invoke("guiShowAnswer")
        return jsonify({"status": "ok"})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/answer")
def api_answer():
    global CURRENT_CARD
    payload = request.get_json(force=True, silent=True) or {}
    card_id = payload.get("cardId")
    ease = payload.get("ease")
    if not isinstance(card_id, int) or not isinstance(ease, int):
        return jsonify({"error": "invalid payload"}), 400
    if not CURRENT_CARD or CURRENT_CARD.get("cardId") != card_id:
        return jsonify({"error": "card mismatch"}), 409
    try:
        ok = invoke("guiAnswerCard", ease=ease)
        if not ok:
            return jsonify({"error": "failed to answer card"}), 500
        CURRENT_CARD = None
        try:
            invoke("guiShowQuestion")
        except RuntimeError:
            pass
        return jsonify({"status": "ok"})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/diff")
def api_diff():
    payload = request.get_json(force=True, silent=True) or {}
    expected = payload.get("expected")
    actual = payload.get("actual")
    diff = build_diff(expected, actual)
    return jsonify(diff)


@app.post("/api/translate")
def api_translate():
    payload = request.get_json(force=True, silent=True) or {}
    text = payload.get("text") or ""
    translation = translate_text(text)
    return jsonify({"translation": translation})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
