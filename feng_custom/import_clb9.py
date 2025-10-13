#!/usr/bin/env python3
"""Import CLB9 language-learning data into Anki via AnkiConnect."""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence

from tqdm import tqdm

API_URL = "http://127.0.0.1:8765"
API_VERSION = 6

DECK_NAME = "English-CLB9"
LISTENING_MODEL = "CLB9 Listening"
WORD_MODEL = "CLB9 Word"
WRITING_MODEL = "CLB9 Writing"

LISTENING_TEMPLATE = "Dictation"
WORD_TEMPLATE = "Spelling"
WRITING_TEMPLATE = "Revision"

DATA_ROOT = Path.home() / "PycharmProjects" / "language-learning" / "data"
DB_PATH = DATA_ROOT / "cases.db"
LISTENING_AUDIO_DIRS = [
    Path("/Users/fenglin/Desktop/code/english_listening_material/IELTS_LISTENING"),
    DATA_ROOT / "listening",
]
ANKI_MEDIA_DIR = Path.home() / "Library" / "Application Support" / "Anki2" / "User 1" / "collection.media"
WORD_VOICES = ["Daniel", "Samantha"]

BASE_CSS = """
.card {
  font-family: "Noto Sans", "Helvetica Neue", Arial, sans-serif;
  font-size: 24px;
  line-height: 1.5;
  color: #1c1c1c;
  background-color: #f8fbff;
  text-align: left;
  padding: 24px;
}
.clb9-prompt {
  font-weight: 600;
  margin-bottom: 16px;
}
.clb9-audio {
  margin-bottom: 12px;
}
.clb9-input,
.clb9-textarea {
  width: 100%;
  font-size: 22px;
  line-height: 1.4;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #c5d6f2;
  box-sizing: border-box;
  margin-bottom: 12px;
}
.clb9-hint {
  font-size: 16px;
  color: #3b5785;
  margin-bottom: 8px;
}
.clb9-answer-box {
  background: #ecf4ff;
  border-left: 4px solid #5b8ceb;
  padding: 12px;
  margin-top: 12px;
}
.clb9-meta {
  font-size: 16px;
  color: #486089;
  margin-top: 16px;
}
"""

LISTENING_FRONT = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">{{Prompt}}</div>
  <div class=\"clb9-audio\">{{Audio}}</div>
  <textarea id=\"clb9-listening-input\" class=\"clb9-textarea\" placeholder=\"请在此输入你听到的句子\" autofocus></textarea>
  <div class=\"clb9-hint\">完成后按空格或点击“显示答案”查看正确文本。</div>
</div>
<script>
(function() {
  window._clb9ListeningAnswer = '';
  const textarea = document.getElementById('clb9-listening-input');
  const save = () => {
    if (textarea) {
      window._clb9ListeningAnswer = textarea.value.trim();
    }
  };
  if (textarea) {
    textarea.focus();
  }
  document.addEventListener('keydown', function(event) {
    if (event.code === 'Space' || event.code === 'Enter') {
      save();
    }
  }, true);
  document.addEventListener('visibilitychange', save);
  window.setInterval(save, 400);
})();
</script>
"""

LISTENING_BACK = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">正确答案：</div>
  <div class=\"clb9-answer-box\">{{Transcript}}</div>
  <div class=\"clb9-answer-box\" id=\"clb9-listening-user\" style=\"margin-top: 12px;\"></div>
  <div class=\"clb9-meta\">{{Source}}</div>
</div>
<script>
(function() {
  const container = document.getElementById('clb9-listening-user');
  if (!container) {
    return;
  }
  const label = document.createElement('strong');
  label.textContent = '你的输入：';
  container.appendChild(label);
  const wrapper = document.createElement('div');
  const userAnswer = (window._clb9ListeningAnswer || '').trim();
  wrapper.textContent = userAnswer || '（未填写）';
  container.appendChild(wrapper);
})();
</script>
"""

WORD_FRONT = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">{{Prompt}}</div>
  <div class=\"clb9-audio\">{{WordAudio}}</div>
  <input id=\"clb9-word-input\" class=\"clb9-input\" type=\"text\" placeholder=\"请在此输入单词拼写\" autofocus />
  <div class=\"clb9-hint\">输入完成后按空格或点击“显示答案”。</div>
</div>
<script>
(function() {
  window._clb9WordAnswer = '';
  const input = document.getElementById('clb9-word-input');
  const save = () => {
    if (input) {
      window._clb9WordAnswer = input.value.trim();
    }
  };
  if (input) {
    input.focus();
  }
  document.addEventListener('keydown', function(event) {
    if (event.code === 'Space' || event.code === 'Enter') {
      save();
    }
  }, true);
  document.addEventListener('visibilitychange', save);
  window.setInterval(save, 400);
})();
</script>
"""

WORD_BACK = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">正确拼写：</div>
  <div class=\"clb9-answer-box\">{{Word}}</div>
  <div class=\"clb9-answer-box\" id=\"clb9-word-user\" style=\"margin-top: 12px;\"></div>
</div>
<script>
(function() {
  const container = document.getElementById('clb9-word-user');
  if (!container) {
    return;
  }
  const label = document.createElement('strong');
  label.textContent = '你的输入：';
  container.appendChild(label);
  const wrapper = document.createElement('div');
  const userAnswer = (window._clb9WordAnswer || '').trim();
  wrapper.textContent = userAnswer || '（未填写）';
  container.appendChild(wrapper);
})();
</script>
"""

WRITING_FRONT = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">{{Prompt}}</div>
  <div class=\"clb9-answer-box\">{{Original}}</div>
</div>
"""

WRITING_BACK = """
<div class=\"clb9-card\">
  <div class=\"clb9-prompt\">修改建议：</div>
  <div class=\"clb9-answer-box\">{{Corrected}}</div>
</div>
"""


class AnkiError(RuntimeError):
    """Raised when AnkiConnect returns an error."""


def invoke(action: str, **params):
    payload = json.dumps({"action": action, "version": API_VERSION, "params": params}).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接到 AnkiConnect：{exc}") from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnkiError(f"解析 AnkiConnect 响应失败：{raw}") from exc

    if result.get("error"):
        raise AnkiError(f"调用 {action} 失败：{result['error']}")
    return result.get("result")


def ensure_deck(deck: str) -> None:
    invoke("createDeck", deck=deck)


def ensure_model(
    model_name: str,
    fields: Sequence[str],
    template_name: str,
    front: str,
    back: str,
    css: str,
) -> None:
    models = set(invoke("modelNames"))
    if model_name not in models:
        invoke(
            "createModel",
            modelName=model_name,
            inOrderFields=list(fields),
            css=css,
            cardTemplates=[
                {
                    "Name": template_name,
                    "Front": front,
                    "Back": back,
                }
            ],
        )
        return

    existing_fields = invoke("modelFieldNames", modelName=model_name)
    for field in fields:
        if field not in existing_fields:
            invoke("modelFieldAdd", modelName=model_name, fieldName=field)

    invoke("updateModelStyling", model={"name": model_name, "css": css})
    templates = invoke("modelTemplates", modelName=model_name)
    if template_name not in templates:
        raise AnkiError(f"模型 {model_name} 缺少模板 {template_name}，请手动检查。")
    invoke(
        "updateModelTemplates",
        model={
            "name": model_name,
            "templates": {
                template_name: {"Front": front, "Back": back},
            },
        },
    )


def parse_time_to_seconds(time_text: str | None) -> float | None:
    if not time_text:
        return None
    parts = time_text.strip().split(":")
    if not parts:
        return None
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 1:
        seconds = parts[0]
    elif len(parts) == 2:
        minutes, seconds = parts
        seconds = minutes * 60 + seconds
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        seconds = hours * 3600 + minutes * 60 + seconds
    else:
        return None
    return seconds


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds_ms, millis = divmod(rem, 1000)
    if hours:
        prefix = f"{hours:02d}{minutes:02d}{seconds_ms:02d}"
    else:
        prefix = f"{minutes:02d}{seconds_ms:02d}"
    if millis:
        return f"{prefix}{millis:03d}"
    return prefix


def clip_audio(source: Path, start: float, end: float, output_name: str) -> str:
    if end <= start:
        raise ValueError("音频结束时间必须大于开始时间")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
        temp_path = Path(handle.name)
    duration = end - start
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}" if start % 1 else f"{int(start)}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}" if duration % 1 else f"{int(duration)}",
        "-c",
        "copy",
        str(temp_path),
    ]
    try:
        subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"ffmpeg 裁剪音频失败：{' '.join(args)}\n{exc.stderr.decode('utf-8', errors='ignore')}"
        ) from exc

    with temp_path.open("rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    temp_path.unlink(missing_ok=True)
    stored_name = invoke("storeMediaFile", filename=output_name, data=encoded)
    if not isinstance(stored_name, str) or not stored_name:
        stored_name = output_name
    return stored_name


def sanitize_word_for_audio(word: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "_", word.lower())
    sanitized = sanitized.strip("_")
    return sanitized or "word"


def ensure_word_audio(word: str) -> str:
    if not word:
        return ""

    sanitized = sanitize_word_for_audio(word)
    filename = f"clb9_word_{sanitized}.mp3"

    if ANKI_MEDIA_DIR.exists():
        media_file = ANKI_MEDIA_DIR / filename
        if media_file.exists() and media_file.stat().st_size > 0:
            return filename

    try:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as aiff_handle:
            aiff_path = Path(aiff_handle.name)
        last_error = None
        for voice in WORD_VOICES:
            say_cmd = ["say", "-v", voice, "-o", str(aiff_path), word]
            try:
                subprocess.run(
                    say_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                break
            except subprocess.CalledProcessError as exc:
                last_error = exc
        else:
            if last_error is not None:
                print(
                    f"生成单词音频失败：{word} -> {last_error.stderr.decode('utf-8', errors='ignore')}"
                )
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

        with mp3_path.open("rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("ascii")
        stored_name = invoke("storeMediaFile", filename=filename, data=encoded)
        if not isinstance(stored_name, str) or not stored_name:
            stored_name = filename
        return stored_name
    except FileNotFoundError:
        print("系统缺少 say 或 ffmpeg 命令，无法生成单词音频。")
        return ""
    except subprocess.CalledProcessError as exc:
        print(
            f"生成单词音频失败：{word} -> {exc.stderr.decode('utf-8', errors='ignore')}"
        )
        return ""
    finally:
        for temp_file in (locals().get("aiff_path"), locals().get("mp3_path")):
            if isinstance(temp_file, Path):
                temp_file.unlink(missing_ok=True)


def stats_text(row: sqlite3.Row) -> str:
    return ""


def sanitize_audio_name(file_name: str, start: float, end: float, note_id: int) -> str:
    base = Path(file_name).stem.replace(' ', '_')
    base = base.replace(':', '').replace('/', '_')
    return f"clb9_listening_{note_id}_{base}_{format_timestamp(start)}-{format_timestamp(end)}.mp3"


def ensure_paths() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"找不到数据库文件：{DB_PATH}")
    if not any(directory.exists() for directory in LISTENING_AUDIO_DIRS):
        raise SystemExit(
            "未找到任何听力音频目录，请确认音频位于 "
            + ", ".join(str(p) for p in LISTENING_AUDIO_DIRS)
        )


def resolve_audio_path(filename: str | None) -> Path | None:
    if not filename:
        return None
    for directory in LISTENING_AUDIO_DIRS:
        candidate = directory / filename
        if candidate.exists():
            return candidate
    return None


def remove_missing_audio_entries(conn: sqlite3.Connection) -> int:
    """Delete listening entries whose source audio file is missing."""

    conn.row_factory = sqlite3.Row
    to_remove: list[sqlite3.Row] = []
    for row in conn.execute("SELECT id, file FROM ALLERROR WHERE type='listening'"):
        path = resolve_audio_path(row["file"])
        if path is None:
            to_remove.append(row)

    if not to_remove:
        return 0

    for row in to_remove:
        note_ids = invoke("findNotes", query=f'DbId:"{row["id"]}"')
        if note_ids:
            invoke("deleteNotes", notes=note_ids)

    conn.executemany("DELETE FROM ALLERROR WHERE id=?", [(row["id"],) for row in to_remove])
    conn.commit()
    return len(to_remove)


def add_or_update_note(fields: Dict[str, str], deck: str, model: str, tags: Sequence[str], guid: str) -> str:
    query = f'DbId:"{fields.get("DbId", "")}"'
    existing = invoke("findNotes", query=query) if fields.get("DbId") else []
    if existing:
        note_id = existing[0]
        invoke("updateNoteFields", note={"id": note_id, "fields": fields})
        if tags:
            invoke("addTags", notes=existing, tags=" ".join(tags))
        return "updated"

    payload = {
        "deckName": deck,
        "modelName": model,
        "fields": fields,
        "tags": list(tags),
        "options": {
            "allowDuplicate": True,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {"deckName": deck, "checkChildren": False},
        },
        "guid": guid,
    }
    invoke("addNotes", notes=[payload])
    return "created"


@dataclass
class Counters:
    created: int = 0
    updated: int = 0


def main() -> None:
    ensure_paths()

    ensure_deck(DECK_NAME)
    ensure_model(
        LISTENING_MODEL,
        fields=["Prompt", "Audio", "Transcript", "Source", "Stats", "DbId"],
        template_name=LISTENING_TEMPLATE,
        front=LISTENING_FRONT,
        back=LISTENING_BACK,
        css=BASE_CSS,
    )
    ensure_model(
        WORD_MODEL,
        fields=["Prompt", "Word", "WordAudio", "Stats", "DbId"],
        template_name=WORD_TEMPLATE,
        front=WORD_FRONT,
        back=WORD_BACK,
        css=BASE_CSS,
    )
    ensure_model(
        WRITING_MODEL,
        fields=["Prompt", "Original", "Corrected", "Stats", "DbId"],
        template_name=WRITING_TEMPLATE,
        front=WRITING_FRONT,
        back=WRITING_BACK,
        css=BASE_CSS,
    )

    conn = sqlite3.connect(DB_PATH)
    removed = remove_missing_audio_entries(conn)
    conn.row_factory = sqlite3.Row
    if removed:
        print(f"已删除 {removed} 条缺少音频文件的听力记录。")

    rows = list(conn.execute("SELECT * FROM ALLERROR"))

    print(f"准备导入 {len(rows)} 条记录…")

    counters: Dict[str, Counters] = {
        "listening": Counters(),
        "word": Counters(),
        "writing": Counters(),
    }

    for row in tqdm(rows, desc="导入中", unit="条"):
        note_type = (row["type"] or "").lower()
        note_id = row["id"]
        tag_type = note_type.capitalize() if note_type else "Unknown"
        common_tags = ["CLB9", tag_type, "AutoImport"]
        stats = stats_text(row)

        if note_type == "listening":
            audio_file = row["file"]
            start_seconds = parse_time_to_seconds(row["start_time"])
            end_seconds = parse_time_to_seconds(row["end_time"])
            if not audio_file or start_seconds is None or end_seconds is None:
                print(f"跳过缺少音频时间信息的记录 {note_id}")
                continue
            source_path = resolve_audio_path(audio_file)
            if not source_path:
                print(
                    "找不到音频文件 "
                    f"{audio_file}（检查目录: {', '.join(str(p) for p in LISTENING_AUDIO_DIRS)}），跳过记录 {note_id}"
                )
                continue
            media_name = sanitize_audio_name(audio_file, start_seconds, end_seconds, note_id)
            clip_audio(source_path, start_seconds, end_seconds, media_name)
            fields = {
                "Prompt": "请听写以下音频内容：",
                "Audio": f"[sound:{media_name}]",
                "Transcript": row["answer"] or "",
                "Source": f"音频：{audio_file} {row['start_time']}-{row['end_time']}",
                "Stats": stats,
                "DbId": str(note_id),
            }
            result = add_or_update_note(
                fields=fields,
                deck=DECK_NAME,
                model=LISTENING_MODEL,
                tags=common_tags,
                guid=f"clb9-listening-{note_id}",
            )

        elif note_type == "word":
            answer = row["answer"] or ""
            audio_file = ensure_word_audio(answer)
            fields = {
                "Prompt": "请听写并拼写以下单词：",
                "Word": answer,
                "WordAudio": f"[sound:{audio_file}]" if audio_file else "",
                "Stats": stats,
                "DbId": str(note_id),
            }
            result = add_or_update_note(
                fields=fields,
                deck=DECK_NAME,
                model=WORD_MODEL,
                tags=common_tags,
                guid=f"clb9-word-{note_id}",
            )

        elif note_type == "writing":
            fields = {
                "Prompt": "请审阅并改写下列句子：",
                "Original": row["question"] or "",
                "Corrected": row["answer"] or "",
                "Stats": stats,
                "DbId": str(note_id),
            }
            result = add_or_update_note(
                fields=fields,
                deck=DECK_NAME,
                model=WRITING_MODEL,
                tags=common_tags,
                guid=f"clb9-writing-{note_id}",
            )

        else:
            print(f"遇到未知类型 {row['type']}，跳过记录 {note_id}")
            continue

        if result == "created":
            counters[note_type].created += 1
        elif result == "updated":
            counters[note_type].updated += 1

    conn.close()

    print("导入完成：")
    for key, counter in counters.items():
        print(f" - {key}: 新增 {counter.created} 张卡片，更新 {counter.updated} 张卡片")


if __name__ == "__main__":
    try:
        main()
    except AnkiError as exc:
        print(exc)
        sys.exit(1)
