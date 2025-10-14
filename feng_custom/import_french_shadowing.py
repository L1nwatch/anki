#!/usr/bin/env python3
"""Import the French shadowing pack into a dedicated Anki deck."""
from __future__ import annotations

import base64
import csv
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import requests


API_URL = "http://127.0.0.1:8765"
API_VERSION = 6

DECK_NAME = "French-NCLC 7"
MODEL_NAME = "French Shadowing"

CSV_PATH = Path(__file__).parent / "data" / "french_A1_to_A2_shadowing_pack.csv"
FRENCH_VOICES = ["Thomas", "Amelie", "Alice", "Claire", "Aurelie"]
ANKI_MEDIA_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Anki2"
    / "User 1"
    / "collection.media"
)


SHADOWING_CSS = """
.shadow-card {
  font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
  font-size: 26px;
  line-height: 1.5;
  color: #0f1a2b;
  background: #f5f8ff;
  border-radius: 14px;
  padding: 28px;
}
.shadow-sentence {
  font-size: 34px;
  font-weight: 700;
  color: #102a66;
  margin-bottom: 16px;
}
.shadow-instruction {
  font-size: 18px;
  color: #506690;
  margin-top: 20px;
}
.shadow-english {
  font-size: 26px;
  color: #1b365d;
  margin-top: 14px;
  font-weight: 600;
}
.shadow-feedback {
  margin-top: 24px;
  padding: 16px;
  border-left: 4px solid #3d7bfd;
  background: rgba(61, 123, 253, 0.12);
  color: #0c1f3d;
  font-size: 20px;
}
.shadow-audio {
  margin-top: 18px;
}
"""


FRONT_TEMPLATE = """
<div class="shadow-card">
  <div class="shadow-sentence">{{ExampleFR}}</div>
  <div class="shadow-instruction">朗读整句，然后点击“显示答案”。</div>
</div>
"""


BACK_TEMPLATE = """
<div class="shadow-card">
  <div class="shadow-sentence">{{ExampleFR}}</div>
  {{#Audio}}<div class="shadow-audio">{{Audio}}</div>{{/Audio}}
  <div class="shadow-english">{{ExampleEN}}</div>
  <div class="shadow-feedback" id="shadow-feedback" style="display:none;"></div>
</div>
<script>
  (function(){
    if (!window._shadowEvaluate) {
      return;
    }
    var payload = {
      sentence: document.querySelector('.shadow-sentence')?.textContent || '',
      word: document.querySelector('.shadow-word')?.textContent || '',
      meaning: document.querySelector('.shadow-english')?.textContent || ''
    };
    window._shadowEvaluate(payload).then(function (message) {
      var box = document.getElementById('shadow-feedback');
      if (!box) {
        return;
      }
      box.style.display = 'block';
      box.textContent = message || '暂无发音反馈。';
    }).catch(function () {});
  })();
</script>
"""


@dataclass
class Row:
  french: str
  ipa: str
  english: str
  example_fr: str
  example_en: str
  tags: List[str]


def invoke(action: str, **params) -> Dict:
  payload = {"action": action, "version": API_VERSION, "params": params}
  resp = requests.post(API_URL, json=payload, timeout=30)
  resp.raise_for_status()
  data = resp.json()
  if data.get("error"):
    raise RuntimeError(data["error"])
  return data.get("result")


def ensure_deck() -> None:
  existing = invoke("deckNames")
  if DECK_NAME in existing:
    return
  invoke("createDeck", deck=DECK_NAME)


def ensure_model() -> None:
  fields = [
      {"name": "French"},
      {"name": "IPA"},
      {"name": "Audio"},
      {"name": "English"},
      {"name": "ExampleFR"},
      {"name": "ExampleEN"},
  ]
  templates = [
      {
          "Name": "Shadowing",
          "Front": FRONT_TEMPLATE,
          "Back": BACK_TEMPLATE,
      }
  ]
  models = invoke("modelNames")
  if MODEL_NAME not in models:
    invoke(
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=[f["name"] for f in fields],
        css=SHADOWING_CSS,
        isCloze=False,
        cardTemplates=templates,
    )
  else:
    existing_fields = invoke("modelFieldNames", modelName=MODEL_NAME) or []
    if "Audio" not in existing_fields:
      invoke("modelFieldAdd", modelName=MODEL_NAME, fieldName="Audio")

  invoke(
      "updateModelTemplates",
      model={
          "name": MODEL_NAME,
          "templates": {
              "Shadowing": {"Front": FRONT_TEMPLATE, "Back": BACK_TEMPLATE}
          },
      },
  )
  invoke(
      "updateModelStyling",
      model={"name": MODEL_NAME, "css": SHADOWING_CSS},
  )


def load_rows() -> List[Row]:
  rows: List[Row] = []
  with CSV_PATH.open(newline="", encoding="utf-8") as handle:
    reader = csv.DictReader(handle)
    for raw in reader:
      tags = [
          tag.strip()
          for tag in (raw.get("Tags") or "").replace(";", ",").split(",")
          if tag.strip()
      ]
      rows.append(
          Row(
              french=(raw.get("French") or "").strip(),
              ipa=(raw.get("IPA") or "").strip(),
              english=(raw.get("English") or "").strip(),
              example_fr=(raw.get("Example (FR)") or "").strip(),
              example_en=(raw.get("Example (EN)") or "").strip(),
              tags=tags,
          )
      )
  return rows


def existing_french_notes() -> Dict[str, Dict[str, Any]]:
  ids = invoke("findNotes", query=f'deck:"{DECK_NAME}"') or []
  if not ids:
    return {}
  infos = invoke("notesInfo", notes=ids) or []

  notes: Dict[str, Dict[str, Any]] = {}

  for info in infos:
    fields = info.get("fields", {})
    sentence_value = (fields.get("ExampleFR", {}).get("value") or "").strip()
    if not sentence_value:
      continue
    key = sentence_value.lower()
    notes[key] = {
        "note_id": info.get("noteId"),
        "sentence": sentence_value,
        "french": (fields.get("French", {}).get("value") or "").strip(),
        "ipa": (fields.get("IPA", {}).get("value") or "").strip(),
        "audio": (fields.get("Audio", {}).get("value") or "").strip(),
        "english": (fields.get("English", {}).get("value") or "").strip(),
        "example_fr": (fields.get("ExampleFR", {}).get("value") or "").strip(),
        "example_en": (fields.get("ExampleEN", {}).get("value") or "").strip(),
    }
  return notes


def sanitize_audio_name(text: str) -> str:
  base = re.sub(r"[^A-Za-z0-9]+", "_", text.strip().lower())
  base = base.strip("_") or "french_word"
  return f"french_shadow_{base}.mp3"


def generate_french_audio(text: str) -> str:
  if not text:
    return ""

  filename = sanitize_audio_name(text)

  if ANKI_MEDIA_DIR.exists():
    media_path = ANKI_MEDIA_DIR / filename
    if media_path.exists() and media_path.stat().st_size > 0:
      return filename

  aiff_path: Path | None = None
  mp3_path: Path | None = None
  try:
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as aiff_handle:
      aiff_path = Path(aiff_handle.name)
    last_error = None
    for voice in FRENCH_VOICES:
      cmd = ["say", "-v", voice, "-o", str(aiff_path), text]
      try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        break
      except subprocess.CalledProcessError as exc:
        last_error = exc
    else:
      if last_error is not None:
        print(
          f"生成法语音频失败：{text} -> {last_error.stderr.decode('utf-8', errors='ignore')}"
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
    print("系统缺少 say 或 ffmpeg 命令，无法生成法语音频。")
    return ""
  except subprocess.CalledProcessError as exc:
    print("生成法语音频失败：", exc)
    return ""
  finally:
    if aiff_path is not None:
      aiff_path.unlink(missing_ok=True)
    if mp3_path is not None:
      mp3_path.unlink(missing_ok=True)


def build_note(row: Row, audio_field: str) -> Dict[str, Any]:
  return {
      "deckName": DECK_NAME,
      "modelName": MODEL_NAME,
      "fields": {
          "French": row.french,
          "IPA": row.ipa,
          "Audio": audio_field,
          "English": row.english,
          "ExampleFR": row.example_fr,
          "ExampleEN": row.example_en,
      },
      "tags": row.tags,
  }


def add_new_notes(notes: List[Dict[str, Any]]) -> int:
  if not notes:
    return 0
  result = invoke("addNotes", notes=notes)
  added = 0
  for status in result:
    if isinstance(status, int) and status > 0:
      added += 1
  return added


def update_existing_notes(notes: List[Dict[str, Any]]) -> int:
  updated = 0
  for note in notes:
    invoke("updateNoteFields", note=note)
    updated += 1
  return updated


def main() -> None:
  if not CSV_PATH.exists():
    raise SystemExit(f"CSV not found: {CSV_PATH}")
  ensure_deck()
  ensure_model()
  rows = load_rows()
  if not rows:
    print("No rows to import.")
    return
  existing = existing_french_notes()

  new_notes: List[Dict[str, Any]] = []
  updates: List[Dict[str, Any]] = []

  for row in rows:
    if not row.french:
      continue
    sentence = row.example_fr
    if not sentence:
      continue
    key = sentence.lower()
    audio_file = generate_french_audio(sentence)
    audio_field = f"[sound:{audio_file}]" if audio_file else ""

    if key not in existing:
      new_notes.append(build_note(row, audio_field))
      continue

    info = existing[key]
    note_id = info.get("note_id")
    if not note_id:
      continue

    fields: Dict[str, str] = {}

    def maybe_update(field_name: str, new_value: str, current: str) -> None:
      normalized_new = (new_value or "").strip()
      if normalized_new and normalized_new != (current or ""):
        fields[field_name] = normalized_new

    maybe_update("French", row.french, info.get("french", ""))
    maybe_update("IPA", row.ipa, info.get("ipa", ""))
    maybe_update("English", row.english, info.get("english", ""))
    maybe_update("ExampleFR", row.example_fr, info.get("sentence", ""))
    maybe_update("ExampleEN", row.example_en, info.get("example_en", ""))
    if audio_field and audio_field != info.get("audio", ""):
      fields["Audio"] = audio_field

    if fields:
      updates.append({"id": note_id, "fields": fields})

  added = add_new_notes(new_notes)
  updated = update_existing_notes(updates) if updates else 0

  if not new_notes and not updates:
    print("所有记录均已存在且无需更新。")
  else:
    print(f"新增 {added} 条，更新 {updated} 条。")


if __name__ == "__main__":
  main()
