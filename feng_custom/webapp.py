#!/usr/bin/env python3
"""Flask web front-end that proxies Anki's reviewer via AnkiConnect."""
from __future__ import annotations

import base64
import difflib
import mimetypes
import os
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, make_response, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).parent

ANKI_CONNECT = "http://127.0.0.1:8765"
DECK_NAME = "English-CLB9"
FRENCH_DECK_NAME = "French-Speaking-NCLC7"
FRENCH_DECK_NAME_YISEN = "French-NCLC7-yisen"
FRENCH_VOCAB_DECK_NAME = "French-NCLC7"
# Include œ/Œ so ligatures are preserved within tokens during diff rendering.
TOKEN_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿŒœ0-9]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿŒœ0-9]+)?|[.,!?;:()\"“”‘’]"
)
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

CURRENT_CARDS: Dict[str, Optional[Dict[str, Any]]] = {
    DECK_NAME: None,
    FRENCH_DECK_NAME: None,
    FRENCH_DECK_NAME_YISEN: None,
    FRENCH_VOCAB_DECK_NAME: None,
}

COOKIE_NAME = "anki_user"

USER_CONFIG: Dict[str, Dict[str, Any]] = {
    "feng": {
        "display_name": "Feng",
        "home_options": [
            {
                "endpoint": "english_index",
                "deck_name": DECK_NAME,
                "title": DECK_NAME,
                "subtitle": "听力、拼写、写作综合练习",
            },
            {
                "endpoint": "french_index",
                "deck_name": FRENCH_DECK_NAME,
                "title": FRENCH_DECK_NAME,
                "subtitle": "A1-A2 影子跟读与发音评分",
            },
            {
                "endpoint": "french_vocab_index",
                "deck_name": FRENCH_VOCAB_DECK_NAME,
                "title": FRENCH_VOCAB_DECK_NAME,
                "subtitle": "法语听写 + 英/中 释义提示",
            },
        ],
        "route_decks": {
            "english": DECK_NAME,
            "french": FRENCH_DECK_NAME,
            "french_vocab": FRENCH_VOCAB_DECK_NAME,
        },
    },
    "yisen": {
        "display_name": "Yisen",
        "home_options": [
            {
                "endpoint": "french_vocab_index",
                "deck_name": FRENCH_DECK_NAME_YISEN,
                "title": FRENCH_DECK_NAME_YISEN,
                "subtitle": "法语听写 + 释义提示",
            }
        ],
        "route_decks": {
            "french_vocab": FRENCH_DECK_NAME_YISEN,
        },
    },
}


def get_current_user() -> Optional[str]:
    username = request.cookies.get(COOKIE_NAME, "").strip().lower()
    if not username:
        return None
    if username not in USER_CONFIG:
        return None
    return username


def get_user_config(username: str) -> Dict[str, Any]:
    return USER_CONFIG.get(username, {})


def resolve_user_deck(route_key: str) -> Optional[str]:
    user = get_current_user()
    if not user:
        return None
    config = get_user_config(user)
    route_map: Dict[str, str] = config.get("route_decks", {})
    return route_map.get(route_key)


def get_authenticated_user() -> Optional[Tuple[str, Dict[str, Any]]]:
    user = get_current_user()
    if not user:
        return None
    config = get_user_config(user)
    if not config:
        return None
    return user, config

OPENAI_AUDIO_MODEL = os.environ.get("OPENAI_AUDIO_MODEL", "gpt-4o-mini-transcribe")
OPENAI_KEY_FILE = BASE_DIR / "data" / "openai_api_key.txt"


def invoke(action: str, **params: Any) -> Any:
    payload = {"action": action, "version": 6, "params": params}
    try:
        resp = requests.post(ANKI_CONNECT, json=payload, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"AnkiConnect 网络请求失败：{exc}") from exc
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError("AnkiConnect 返回无法解析的 JSON 数据。") from exc
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


def load_openai_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        return api_key
    try:
        text = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    except FileNotFoundError:
        pass
    raise RuntimeError(
        "缺少 OpenAI API KEY，请设置环境变量 OPENAI_API_KEY 或在 data/openai_api_key.txt 中提供。"
    )


def transcribe_with_openai(path: Path, content_type: str | None = None) -> str:
    api_key = load_openai_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"model": OPENAI_AUDIO_MODEL, "language": "fr"}
    url = "https://api.openai.com/v1/audio/transcriptions"
    mime_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    try:
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, mime_type)}
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        return (body.get("text") or "").strip()
    except requests.RequestException as exc:  # pragma: no cover - network
        raise RuntimeError(f"OpenAI 语音转写失败：{exc}") from exc
    except ValueError as exc:  # pragma: no cover - invalid JSON
        raise RuntimeError("OpenAI 返回无法解析的 JSON 数据。") from exc


def normalize_for_compare(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-zœæç'\-]+", " ", stripped.lower()).strip()


def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = prev_row[j] + 1
            replace_cost = prev_row[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        prev_row = current
    return prev_row[-1]


def similarity_score(expected: str, actual: str) -> float:
    clean_expected = normalize_for_compare(expected)
    clean_actual = normalize_for_compare(actual)
    if not clean_expected or not clean_actual:
        return 0.0
    distance = levenshtein_distance(clean_expected, clean_actual)
    max_len = max(len(clean_expected), len(clean_actual))
    if max_len == 0:
        return 0.0
    return max(0.0, 1.0 - distance / max_len)


def feedback_for_score(score: float) -> str:
    pct = round(score * 100)
    if score >= 0.85:
        return f"匹配度 {pct}%：发音非常接近，继续保持！"
    if score >= 0.6:
        return f"匹配度 {pct}%：不错，可以再注意重音或连读。"
    if score > 0:
        return f"匹配度 {pct}%：识别偏差较大，建议重新听原音后再试。"
    return "未识别到有效文本，请再试一次。"


def deck_counts(deck_name: str) -> Dict[str, int]:
    names = invoke("deckNamesAndIds") or {}
    deck_id = names.get(deck_name)
    if deck_id is None:
        return {
            "due": 0,
            "new": 0,
            "learning": 0,
            "words_total": 0,
            "words_learned": 0,
            "words_remaining": 0,
        }
    stats_map = invoke("getDeckStats", decks=[deck_name]) or {}
    stats = stats_map.get(str(deck_id), {})
    safe_deck = deck_name.replace('"', '\\"')

    def _note_count(extra_query: str = "") -> int:
        query = f'deck:"{safe_deck}" {extra_query}'.strip()
        try:
            notes = invoke("findNotes", query=query) or []
        except RuntimeError:
            return 0
        return len(notes)

    total_notes = _note_count()
    remaining_notes = _note_count("is:new")
    learned_notes = max(total_notes - remaining_notes, 0)

    return {
        "due": stats.get("review_count", 0),
        "new": stats.get("new_count", 0),
        "learning": stats.get("learn_count", 0),
        "words_total": total_notes,
        "words_learned": learned_notes,
        "words_remaining": remaining_notes,
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


def ensure_review_ready(deck_name: str) -> Dict[str, Any]:
    try:
        card = invoke("guiCurrentCard")
        if card and card.get("deckName") == deck_name:
            return card
    except RuntimeError:
        pass
    invoke("guiDeckReview", name=deck_name)
    card = invoke("guiCurrentCard")
    if not card or card.get("deckName") != deck_name:
        raise RuntimeError("no cards available")
    try:
        invoke("guiShowQuestion")
    except RuntimeError:
        pass
    return card


def build_clb9_payload(card: Dict[str, Any]) -> Dict[str, Any]:
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

    return {"type": note_type, "data": data}


def build_french_payload(card: Dict[str, Any]) -> Dict[str, Any]:
    fields = card.get("fields", {})
    audio_file = extract_sound(fields.get("Audio", {}).get("value", ""))
    sentence = fields.get("ExampleFR", {}).get("value", "")
    word = fields.get("French", {}).get("value", "")
    data = {
        "sentence": sentence,
        "word": word,
        "english": fields.get("ExampleEN", {}).get("value", ""),
        "exampleFr": sentence,
        "audioUrl": media_to_data_url(audio_file) if audio_file else None,
    }
    return {"type": "shadowing", "data": data}


def build_french_vocab_payload(card: Dict[str, Any]) -> Dict[str, Any]:
    fields = card.get("fields", {})
    audio_file = extract_sound(fields.get("Audio", {}).get("value", ""))
    data = {
        "prompt": "请听写以下法语单词：",
        "word": fields.get("French", {}).get("value", ""),
        "english": fields.get("English", {}).get("value", ""),
        "chinese": fields.get("Chinese", {}).get("value", ""),
        "audioUrl": media_to_data_url(audio_file) if audio_file else None,
    }
    return {"type": "french_vocab", "data": data}


PAYLOAD_BUILDERS = {
    DECK_NAME: build_clb9_payload,
    FRENCH_DECK_NAME: build_french_payload,
    FRENCH_DECK_NAME_YISEN: build_french_vocab_payload,
    FRENCH_VOCAB_DECK_NAME: build_french_vocab_payload,
}


def build_payload(deck_name: str, card: Dict[str, Any]) -> Dict[str, Any]:
    builder = PAYLOAD_BUILDERS.get(deck_name)
    if builder is None:
        return {
            "type": "generic",
            "data": {
                "prompt": card.get("question", ""),
                "answer": card.get("answer", ""),
            },
        }
    return builder(card)


def set_current_card(deck_name: str, card_id: Optional[int], buttons: List[int]) -> None:
    CURRENT_CARDS[deck_name] = {
        "cardId": card_id,
        "buttons": buttons,
    }


def current_card_state(deck_name: str) -> Optional[Dict[str, Any]]:
    return CURRENT_CARDS.get(deck_name)


def handle_next(deck_name: str) -> Any:
    try:
        card = ensure_review_ready(deck_name)
    except RuntimeError:
        set_current_card(deck_name, None, [])
        return jsonify({"error": "no cards due"}), 404

    try:
        invoke("stopAudio")
        time.sleep(0.2)
        invoke("stopAudio")
    except RuntimeError:
        pass

    buttons = card.get("buttons", []) or [1, 2, 3, 4]
    set_current_card(deck_name, card.get("cardId"), buttons)
    counts = deck_counts(deck_name)
    payload = build_payload(deck_name, card)

    return jsonify(
        {
            "cardId": card.get("cardId"),
            "type": payload.get("type", "generic"),
            "data": payload.get("data", {}),
            "questionHtml": card.get("question", ""),
            "answerHtml": card.get("answer", ""),
            "buttons": buttons,
            "counts": counts,
        }
    )


def handle_reveal(deck_name: str) -> Any:
    state = current_card_state(deck_name)
    if not state or not state.get("cardId"):
        return jsonify({"error": "no active card"}), 409
    try:
        card = ensure_review_ready(deck_name)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    if card.get("cardId") != state.get("cardId"):
        return jsonify({"error": "card mismatch"}), 409
    try:
        invoke("guiShowAnswer")
        return jsonify({"status": "ok"})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500


def handle_answer(deck_name: str) -> Any:
    payload = request.get_json(force=True, silent=True) or {}
    card_id = payload.get("cardId")
    ease = payload.get("ease")
    if not isinstance(card_id, int) or not isinstance(ease, int):
        return jsonify({"error": "invalid payload"}), 400
    state = current_card_state(deck_name)
    if not state or state.get("cardId") != card_id:
        return jsonify({"error": "card mismatch"}), 409
    try:
        card = ensure_review_ready(deck_name)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    if card.get("cardId") != card_id:
        # 如果前一次答题已在 Anki 端生效（例如网络异常导致响应丢失），
        # 这里直接视为成功并清理状态，让前端刷新到下一张卡片。
        set_current_card(deck_name, None, [])
        try:
            invoke("guiShowQuestion")
        except RuntimeError:
            pass
        return jsonify({"status": "ok", "cardAdvanced": True})
    try:
        ok = invoke("guiAnswerCard", ease=ease)
        if not ok:
            return jsonify({"error": "failed to answer card"}), 500
        set_current_card(deck_name, None, [])
        try:
            invoke("guiShowQuestion")
        except RuntimeError:
            pass
        return jsonify({"status": "ok"})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/")
def index():
    user_info = get_authenticated_user()
    if not user_info:
        return render_template("user_select.html", error=None, entered="")

    username, config = user_info
    deck_options = []
    for option in config.get("home_options", []):
        deck_name = option.get("deck_name", "")
        counts = deck_counts(deck_name)
        deck_options.append(
            {
                "endpoint": option.get("endpoint"),
                "deck_name": deck_name,
                "title": option.get("title", deck_name),
                "subtitle": option.get("subtitle", ""),
                "counts": counts,
            }
        )

    return render_template(
        "home.html",
        deck_options=deck_options,
        username=username,
        display_name=config.get("display_name", username),
    )


@app.post("/select-user")
def select_user():
    raw_username = request.form.get("username", "")
    username = raw_username.strip().lower()
    if username in USER_CONFIG:
        response = make_response(redirect(url_for("index")))
        # Store the cookie for roughly 90 days to avoid frequent prompts.
        response.set_cookie(
            COOKIE_NAME,
            username,
            max_age=60 * 60 * 24 * 90,
            samesite="Lax",
            httponly=True,
        )
        return response

    error = "未知用户，请输入已配置的名称。"
    return render_template("user_select.html", error=error, entered=raw_username)


@app.get("/english")
def english_index():
    user_info = get_authenticated_user()
    if not user_info:
        return redirect(url_for("index"))
    username, config = user_info
    deck_name = resolve_user_deck("english")
    if not deck_name:
        return redirect(url_for("index"))
    return render_template(
        "index.html",
        deck_name=deck_name,
        username=username,
        display_name=config.get("display_name", username),
    )


@app.get("/api/next")
def api_next():
    deck_name = resolve_user_deck("english")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_next(deck_name)


@app.post("/api/reveal")
def api_reveal():
    deck_name = resolve_user_deck("english")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_reveal(deck_name)


@app.post("/api/answer")
def api_answer():
    deck_name = resolve_user_deck("english")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_answer(deck_name)


@app.get("/french")
def french_index():
    user_info = get_authenticated_user()
    if not user_info:
        return redirect(url_for("index"))
    username, config = user_info
    deck_name = resolve_user_deck("french")
    if not deck_name:
        return redirect(url_for("index"))
    return render_template(
        "french.html",
        deck_name=deck_name,
        username=username,
        display_name=config.get("display_name", username),
    )


@app.get("/api/fr/next")
def api_french_next():
    deck_name = resolve_user_deck("french")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_next(deck_name)


@app.post("/api/fr/reveal")
def api_french_reveal():
    deck_name = resolve_user_deck("french")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_reveal(deck_name)


@app.post("/api/fr/answer")
def api_french_answer():
    deck_name = resolve_user_deck("french")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_answer(deck_name)


@app.post("/api/fr/analyze")
def api_french_analyze():
    deck_name = resolve_user_deck("french")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403

    state = current_card_state(deck_name)
    if not state or not state.get("cardId"):
        return jsonify({"error": "no active card"}), 409

    try:
        card_id = int(request.form.get("cardId", ""))
    except ValueError:
        return jsonify({"error": "invalid card id"}), 400

    if state.get("cardId") != card_id:
        return jsonify({"error": "card mismatch"}), 409

    audio_file = request.files.get("audio")
    if audio_file is None or not audio_file.filename:
        return jsonify({"error": "audio file missing"}), 400

    temp_path = None
    try:
        original_name = audio_file.filename or "recording.webm"
        guessed_suffix = Path(original_name).suffix or ".webm"
        content_type = audio_file.mimetype or mimetypes.guess_type(original_name)[0]

        with tempfile.NamedTemporaryFile(suffix=guessed_suffix, delete=False) as handle:
            audio_file.save(handle)
            temp_path = Path(handle.name)

        try:
            card = ensure_review_ready(deck_name)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        if card.get("cardId") != card_id:
            return jsonify({"error": "card mismatch"}), 409

        try:
            transcript = transcribe_with_openai(temp_path, content_type=content_type)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503

        expected = card.get("fields", {}).get("French", {}).get("value", "") or ""
        score = similarity_score(expected, transcript)
        feedback = feedback_for_score(score)
        return jsonify(
            {
                "transcript": transcript,
                "score": score,
                "feedback": feedback,
            }
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@app.get("/french-vocab")
def french_vocab_index():
    user_info = get_authenticated_user()
    if not user_info:
        return redirect(url_for("index"))
    username, config = user_info
    deck_name = resolve_user_deck("french_vocab")
    if not deck_name:
        return redirect(url_for("index"))
    return render_template(
        "french_vocab.html",
        deck_name=deck_name,
        username=username,
        display_name=config.get("display_name", username),
    )


@app.get("/api/fv/next")
def api_french_vocab_next():
    deck_name = resolve_user_deck("french_vocab")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_next(deck_name)


@app.post("/api/fv/reveal")
def api_french_vocab_reveal():
    deck_name = resolve_user_deck("french_vocab")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_reveal(deck_name)


@app.post("/api/fv/answer")
def api_french_vocab_answer():
    deck_name = resolve_user_deck("french_vocab")
    if not deck_name:
        return jsonify({"error": "用户未被授权访问该牌组"}), 403
    return handle_answer(deck_name)


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
