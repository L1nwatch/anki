#!/usr/bin/env python3
"""Interactive tool to slice listening audio and add selected segments to Anki."""
from __future__ import annotations

import argparse
import hashlib
import os
import pickle
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from io import BytesIO

from flask import Flask, jsonify, make_response, render_template_string, request, send_file

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from import_clb9 import (  # type: ignore
    AnkiError,
    BASE_CSS,
    DECK_NAME,
    LISTENING_BACK,
    LISTENING_FRONT,
    LISTENING_MODEL,
    LISTENING_TEMPLATE,
    add_or_update_note,
    clip_audio,
    ensure_deck,
    ensure_model,
    format_timestamp,
)

try:
    import whisper  # type: ignore
except ImportError:  # pragma: no cover
    whisper = None  # type: ignore

SILENCE_THRESHOLD_DB = -38.0
MIN_SILENCE_DURATION = 0.45
MIN_SEGMENT_DURATION = 1.2
DEFAULT_PROMPT = "请听写以下音频内容："
DEFAULT_TAGS = ["CLB9", "Listening", "ManualPick"]

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@dataclass
class Segment:
    ident: int
    start: float
    end: float
    transcript: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# Shared state populated in main() / load_audio_file()
AUDIO_PATH: Path = Path()
SEGMENTS: List[Segment] = []
AUDIO_DURATION: float = 0.0
AUDIO_LOADED = False
SEGMENT_LOCK = threading.Lock()
TRANSCRIPTION_NOTICE: str = ""

WHISPER_MODEL_NAME = os.environ.get("CLB9_WHISPER_MODEL", "small")
_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()

INDEX_HTML = """<!doctype html>
<html lang=\"zh\">
<head>
<meta charset=\"utf-8\">
<title>CLB9 听力选段工具</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; background: #f6f9ff; color: #213047; }
h1 { font-size: 26px; margin-bottom: 12px; }
summary { cursor: pointer; }
section { background: #ffffff; border-radius: 10px; padding: 18px; margin-bottom: 20px; box-shadow: 0 6px 16px rgba(48,82,146,0.12); }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th, td { padding: 8px; border-bottom: 1px solid #d7e3ff; text-align: left; font-size: 14px; vertical-align: top; }
button { padding: 6px 16px; border-radius: 6px; border: none; background: #3c6ef7; color: white; cursor: pointer; }
button:disabled { background: #99b3ff; cursor: not-allowed; }
.time-input { width: 84px; text-align: center; padding: 4px 6px; border: 1px solid #c5d6f2; border-radius: 6px; box-sizing: border-box; font-size: 13px; }
.time-input:focus { outline: none; border-color: #6f8de8; box-shadow: 0 0 0 2px rgba(63,105,224,0.15); }
textarea { width: 100%; padding: 10px 12px; border: 1px solid #c5d6f2; border-radius: 6px; box-sizing: border-box; font-size: 14px; resize: vertical; min-height: 110px; line-height: 1.5; }
.segment-row { background: #fff; }
.notice { color: #3c6ef7; font-size: 14px; margin: 12px 0; }
#messages { margin-top: 12px; font-size: 14px; }
.badge { display: inline-block; padding: 2px 6px; border-radius: 4px; background: #f0f4ff; color: #3c4c86; font-size: 12px; margin-right: 6px; }
.control-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.checkbox-cell { text-align: center; }
.index-cell { text-align: center; font-weight: 600; color: #40507a; }
.time-cell { white-space: nowrap; }
.duration-cell { width: 90px; color: #516a9e; }
.small { font-size: 12px; color: #516a9e; }
.preview-cell { display: flex; align-items: center; gap: 8px; }
.preview-cell audio { display: none; width: 220px; height: 32px; }
.transcript-note { color: #516a9e; margin: 12px 0; font-size: 13px; }
.transcript-cell { width: 100%; }
.audio-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.audio-bar input[type="text"] { flex: 1; padding: 8px 12px; border: 1px solid #c5d6f2; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
.audio-bar input[type="text"]:focus { outline: none; border-color: #6f8de8; box-shadow: 0 0 0 2px rgba(63,105,224,0.15); }
.audio-bar label { font-size: 12px; color: #516a9e; display: flex; align-items: center; gap: 6px; }
</style>
</head>
<body>
<h1>CLB9 听力选段工具</h1>
<section>
  <div class=\"audio-bar\">
    <input type=\"text\" id=\"audio-path\" placeholder=\"输入音频文件路径\" value=\"{{ audio_path }}\" />
    <label><input type=\"checkbox\" id=\"no-cache\"> 忽略缓存</label>
    <button id=\"load-audio\" type=\"button\">加载音频</button>
  </div>
  <div class=\"notice\">当前音频：<strong id=\"audio-name\">{{ audio_name }}</strong>（总时长：<span id=\"audio-duration\">{{ duration_label }}</span>）</div>
  <div class=\"notice\">步骤：1) 试听并微调起止时间  2) 核对自动转写文本  3) 选择想要导入的句子并点击“添加到 Anki”</div>
  <div class=\"transcript-note\" id=\"transcription-notice\"{% if not transcription_notice %} style=\"display:none;\"{% endif %}>{{ transcription_notice }}</div>
  <div class=\"control-bar\">
    <div>
      <span class=\"badge\">静音阈值 {{ silence }} dB</span>
      <span class=\"badge\">最短段长 {{ min_segment }} s</span>
    </div>
    <button id=\"add-selected\">添加到 Anki</button>
  </div>
  <table>
    <colgroup>
      <col style="width:40px">
      <col style="width:52px">
      <col style="width:120px">
      <col style="width:120px">
      <col style="width:110px">
      <col style="width:230px">
      <col>
    </colgroup>
    <thead>
      <tr>
        <th class=\"checkbox-cell\"><input type=\"checkbox\" id=\"toggle-all\"></th>
        <th>序号</th>
        <th>开始 (秒)</th>
        <th>结束 (秒)</th>
        <th>时长</th>
        <th>试听</th>
        <th>听写文本</th>
      </tr>
    </thead>
    <tbody id=\"segments\"></tbody>
  </table>
  <div id=\"messages\"></div>
</section>
<script>
const tableBody = document.getElementById('segments');
const messages = document.getElementById('messages');
const toggleAll = document.getElementById('toggle-all');
const addSelectedBtn = document.getElementById('add-selected');
const audioNameEl = document.getElementById('audio-name');
const audioDurationEl = document.getElementById('audio-duration');
const audioPathInput = document.getElementById('audio-path');
const noCacheCheckbox = document.getElementById('no-cache');
const loadAudioBtn = document.getElementById('load-audio');
const transcriptionNoticeEl = document.getElementById('transcription-notice');

if (addSelectedBtn) {
  addSelectedBtn.disabled = true;
}

function showMessage(text, type = 'info') {
  const colors = { info: '#2f5fbf', success: '#2e8547', error: '#c0392b' };
  messages.textContent = text;
  messages.style.color = colors[type] || colors.info;
}

async function loadSegments() {
  const resp = await fetch('/api/segments');
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || '无法获取分段信息');
  }
  const data = await resp.json();
  tableBody.innerHTML = '';
  if (toggleAll) {
    toggleAll.checked = false;
  }
  data.forEach((seg, index) => {
    const tr = document.createElement('tr');
    tr.className = 'segment-row';
    tr.dataset.segmentId = seg.id;

    tr.innerHTML = `
      <td class="checkbox-cell"><input type="checkbox" class="seg-check"></td>
      <td class="index-cell">${index + 1}</td>
      <td class="time-cell"><input type="text" class="start time-input" value="${seg.start}"/></td>
      <td class="time-cell"><input type="text" class="end time-input" value="${seg.end}"/></td>
      <td class="duration-cell small">${seg.duration_label}</td>
      <td class="preview-cell">
        <button type="button" class="preview">试听</button>
        <audio class="player" preload="none" controls></audio>
      </td>
      <td class="transcript-cell"><textarea class="transcript" placeholder="自动转写结果，可根据需要修改"></textarea></td>
    `;
    const transcriptArea = tr.querySelector('.transcript');
    if (transcriptArea) {
      transcriptArea.value = seg.transcript || '';
    }
    tableBody.appendChild(tr);
  });
  if (addSelectedBtn) {
    addSelectedBtn.disabled = !data.length;
  }
  return data;
}

async function loadAppState() {
  const resp = await fetch('/api/app_state');
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || '无法获取工具状态');
  }
  const data = await resp.json();
  if (audioNameEl) {
    audioNameEl.textContent = data.audio_name || '未选择音频';
  }
  if (audioDurationEl) {
    audioDurationEl.textContent = data.duration_label || '00:00.000';
  }
  if (audioPathInput && typeof data.audio_path === 'string') {
    audioPathInput.value = data.audio_path;
  }
  if (transcriptionNoticeEl) {
    if (data.transcription_notice) {
      transcriptionNoticeEl.textContent = data.transcription_notice;
      transcriptionNoticeEl.style.display = '';
    } else {
      transcriptionNoticeEl.textContent = '';
      transcriptionNoticeEl.style.display = 'none';
    }
  }
  return data;
}

tableBody.addEventListener('click', (event) => {
  if (event.target.classList.contains('preview')) {
    const row = event.target.closest('tr');
    const segmentId = row.dataset.segmentId;
    const start = row.querySelector('.start').value.trim();
    const end = row.querySelector('.end').value.trim();
    const audioEl = row.querySelector('.player');
    const button = event.target;
    const url = `/api/segment_audio/${segmentId}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&_=${Date.now()}`;
    button.disabled = true;
    audioEl.src = url;
    audioEl.style.display = 'block';
    audioEl.load();
    const playPromise = audioEl.play();
    if (playPromise !== undefined) {
      playPromise.catch((error) => {
        showMessage(`播放失败：${error}`, 'error');
      }).finally(() => {
        button.disabled = false;
      });
    } else {
      button.disabled = false;
    }
  }
});

toggleAll.addEventListener('change', () => {
  const checked = toggleAll.checked;
  document.querySelectorAll('.seg-check').forEach(cb => {
    cb.checked = checked;
  });
});

addSelectedBtn.addEventListener('click', async () => {
  const payload = { segments: [] };
  document.querySelectorAll('.segment-row').forEach(row => {
    const checkbox = row.querySelector('.seg-check');
    if (!checkbox.checked) {
      return;
    }
    const segmentId = row.dataset.segmentId;
    const start = row.querySelector('.start').value.trim();
    const end = row.querySelector('.end').value.trim();
    const transcript = row.querySelector('.transcript').value.trim();
    payload.segments.push({ id: segmentId, start, end, transcript });
  });

  if (!payload.segments.length) {
    showMessage('请选择至少一个句子再提交。', 'info');
    return;
  }

  addSelectedBtn.disabled = true;
  showMessage('正在添加到 Anki，请稍候…');

  try {
    const resp = await fetch('/api/add_notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || '请求失败');
    }
    const result = await resp.json();
    showMessage(`已新增 ${result.created} 条，更新 ${result.updated} 条。`, 'success');
  } catch (error) {
    showMessage(`添加失败：${error}`, 'error');
  } finally {
    addSelectedBtn.disabled = false;
  }
});

if (loadAudioBtn) {
  loadAudioBtn.addEventListener('click', async () => {
    if (!audioPathInput) {
      return;
    }
    const path = audioPathInput.value.trim();
    if (!path) {
      showMessage('请先输入音频文件路径。', 'info');
      return;
    }
    loadAudioBtn.disabled = true;
    if (addSelectedBtn) {
      addSelectedBtn.disabled = true;
    }
    tableBody.innerHTML = '';
    showMessage('正在加载音频，请稍候…');
    try {
      const resp = await fetch('/api/load_audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          audio: path,
          no_cache: noCacheCheckbox ? noCacheCheckbox.checked : false,
        }),
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || '请求失败');
      }
      await loadAppState();
      await loadSegments();
      showMessage('音频加载完成，可以开始筛选。', 'success');
    } catch (error) {
      showMessage(`加载音频失败：${error}`, 'error');
    } finally {
      loadAudioBtn.disabled = false;
    }
  });
}

async function init() {
  try {
    await loadAppState();
    await loadSegments();
  } catch (error) {
    showMessage(`初始化失败：${error}`, 'error');
  }
}

init();
</script>
</body>
</html>"""


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = proc.stdout.decode("utf-8", errors="ignore").strip()
    try:
        return float(text)
    except ValueError as exc:
        raise RuntimeError(f"无法解析音频时长：{text}") from exc


def detect_segments(path: Path, duration: float) -> List[Segment]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={MIN_SILENCE_DURATION}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    stderr = proc.stderr.decode("utf-8", errors="ignore")

    silence_events: List[Tuple[str, float]] = []
    for line in stderr.splitlines():
        if "silence_start" in line:
            try:
                value = float(line.rsplit("silence_start:", 1)[1].strip())
            except ValueError:
                continue
            silence_events.append(("start", value))
        elif "silence_end" in line:
            try:
                value = float(line.rsplit("silence_end:", 1)[1].split("|")[0].strip())
            except ValueError:
                continue
            silence_events.append(("end", value))

    silence_events.sort(key=lambda item: item[1])

    segments: List[Segment] = []
    current_start = 0.0
    ident_counter = 1
    for event_type, stamp in silence_events:
        if event_type == "start":
            segment_end = stamp
            if segment_end - current_start >= MIN_SEGMENT_DURATION:
                segments.append(Segment(ident=ident_counter, start=current_start, end=segment_end))
                ident_counter += 1
        elif event_type == "end":
            current_start = stamp

    if duration - current_start >= MIN_SEGMENT_DURATION:
        segments.append(Segment(ident=ident_counter, start=current_start, end=duration))

    return segments


def format_seconds_label(value: float) -> str:
    total_ms = int(round(value * 1000))
    minutes, rem = divmod(total_ms, 60000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def ensure_listening_model() -> None:
    ensure_deck(DECK_NAME)
    ensure_model(
        LISTENING_MODEL,
        fields=["Prompt", "Audio", "Transcript", "Source", "Stats", "DbId"],
        template_name=LISTENING_TEMPLATE,
        front=LISTENING_FRONT,
        back=LISTENING_BACK,
        css=BASE_CSS,
    )


def segment_preview_bytes(path: Path, start: float, end: float) -> bytes:
    if end <= start:
        raise ValueError("end must be greater than start")
    duration = end - start
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}" if start % 1 else f"{int(start)}",
        "-i",
        str(path),
        "-t",
        f"{duration:.3f}" if duration % 1 else f"{int(duration)}",
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-f",
        "mp3",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout


def get_whisper_model():
    if whisper is None:
        raise RuntimeError("whisper 未安装，无法自动转写。")
    global _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            _WHISPER_MODEL = whisper.load_model(WHISPER_MODEL_NAME)
    return _WHISPER_MODEL


def transcribe_audio_segments(path: Path) -> List[Dict[str, Any]]:
    model = get_whisper_model()
    result = model.transcribe(
        str(path),
        language="en",
        task="transcribe",
        verbose=False,
    )
    segments = result.get("segments") or []
    output: List[Dict[str, Any]] = []
    for item in segments:
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        text = (item.get("text") or "").strip()
        output.append({"start": start, "end": end, "text": text})
    return output


def attach_transcripts(segments: List[Segment], transcript_chunks: List[Dict[str, Any]]) -> None:
    if not segments or not transcript_chunks:
        return

    texts_by_segment: Dict[int, List[str]] = {seg.ident: [] for seg in segments}

    for chunk in transcript_chunks:
        try:
            chunk_start = float(chunk.get("start", 0.0))
            chunk_end = float(chunk.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        text = (chunk.get("text") or "").strip()
        if not text:
            continue

        chunk_len = max(0.01, chunk_end - chunk_start)
        chunk_mid = (chunk_start + chunk_end) / 2
        best_seg: Segment | None = None
        best_overlap_ratio = 0.0
        fallback_seg: Segment | None = None
        fallback_distance = float("inf")

        for seg in segments:
            seg_mid = (seg.start + seg.end) / 2
            distance = abs(seg_mid - chunk_mid)
            overlap = min(seg.end, chunk_end) - max(seg.start, chunk_start)
            overlap_ratio = max(0.0, overlap) / chunk_len
            if overlap_ratio > best_overlap_ratio:
                best_overlap_ratio = overlap_ratio
                best_seg = seg
            if distance < fallback_distance:
                fallback_distance = distance
                fallback_seg = seg

        assigned_seg = None
        if best_seg and best_overlap_ratio >= 0.12:
            assigned_seg = best_seg
        elif fallback_seg and fallback_distance <= 1.2:
            assigned_seg = fallback_seg
        elif best_seg:
            assigned_seg = best_seg

        if assigned_seg:
            bucket = texts_by_segment.setdefault(assigned_seg.ident, [])
            if not bucket or bucket[-1] != text:
                bucket.append(text)

    for seg in segments:
        combined = " ".join(texts_by_segment.get(seg.ident, [])).strip()
        if combined:
            seg.transcript = combined

def build_note_identifiers(start: float, end: float) -> Tuple[int, str, str]:
    key = f"{AUDIO_PATH.name}-{start:.3f}-{end:.3f}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    numeric_id = int(digest[:8], 16)
    db_id = f"manual-listening-{digest}"
    guid = f"clb9-listening-manual-{digest}"
    return numeric_id, db_id, guid


@app.route("/")
def index() -> str:
    with SEGMENT_LOCK:
        loaded = AUDIO_LOADED
        audio_name = AUDIO_PATH.name if loaded else "未选择音频"
        duration_label = format_seconds_label(AUDIO_DURATION) if loaded else "00:00.000"
        transcription_notice = TRANSCRIPTION_NOTICE if loaded else ""
        audio_path = str(AUDIO_PATH) if loaded else ""
    return render_template_string(
        INDEX_HTML,
        audio_name=audio_name,
        duration_label=duration_label,
        audio_path=audio_path,
        silence=SILENCE_THRESHOLD_DB,
        min_segment=MIN_SEGMENT_DURATION,
        transcription_notice=transcription_notice,
    )


@app.route("/api/segments")
def api_segments():
    with SEGMENT_LOCK:
        payload = [
            {
                "id": str(seg.ident),
                "start": f"{seg.start:.3f}",
                "end": f"{seg.end:.3f}",
                "duration": seg.duration,
                "duration_label": format_seconds_label(seg.duration),
                "transcript": seg.transcript,
            }
            for seg in SEGMENTS
        ]
    return jsonify(payload)


@app.route("/api/app_state")
def api_app_state():
    with SEGMENT_LOCK:
        loaded = AUDIO_LOADED
        audio_path = str(AUDIO_PATH) if loaded else ""
        audio_name = AUDIO_PATH.name if loaded else ""
        duration_label = format_seconds_label(AUDIO_DURATION) if loaded else "00:00.000"
        notice = TRANSCRIPTION_NOTICE if loaded else ""
        segment_count = len(SEGMENTS)
    return jsonify(
        {
            "loaded": loaded,
            "audio_path": audio_path,
            "audio_name": audio_name,
            "duration_label": duration_label,
            "transcription_notice": notice,
            "segment_count": segment_count,
            "silence": SILENCE_THRESHOLD_DB,
            "min_segment": MIN_SEGMENT_DURATION,
        }
    )


@app.route("/api/load_audio", methods=["POST"])
def api_load_audio():
    try:
        payload = request.get_json(force=True)
    except Exception:
        return make_response("Invalid JSON body", 400)

    audio_value = payload.get("audio") if isinstance(payload, dict) else None
    if not isinstance(audio_value, str) or not audio_value.strip():
        return make_response("`audio` 字段必须是字符串路径", 400)

    no_cache = bool(payload.get("no_cache")) if isinstance(payload, dict) else False
    candidate_path = Path(audio_value.strip())

    try:
        load_audio_file(candidate_path, use_cache=not no_cache)
    except FileNotFoundError as exc:
        missing = getattr(exc, "filename", str(candidate_path.expanduser()))
        return make_response(f"找不到音频文件：{missing}", 404)
    except RuntimeError as exc:
        return make_response(str(exc), 400)
    except Exception as exc:
        return make_response(str(exc), 500)

    with SEGMENT_LOCK:
        response = {
            "audio_name": AUDIO_PATH.name,
            "audio_path": str(AUDIO_PATH),
            "duration_label": format_seconds_label(AUDIO_DURATION),
            "segments": len(SEGMENTS),
            "transcription_notice": TRANSCRIPTION_NOTICE,
        }

    return jsonify(response)


@app.route("/api/segment_audio/<segment_id>")
def api_segment_audio(segment_id: str):
    start_param = request.args.get("start")
    end_param = request.args.get("end")
    try:
        if start_param is not None and end_param is not None:
            start = float(start_param)
            end = float(end_param)
        else:
            with SEGMENT_LOCK:
                seg = next((s for s in SEGMENTS if str(s.ident) == segment_id), None)
            if seg is None:
                return make_response("Segment not found", 404)
            start = seg.start
            end = seg.end
        print(f"[preview] segment={segment_id} start={start:.3f} end={end:.3f}")
        data = segment_preview_bytes(AUDIO_PATH, start, end)
    except ValueError as exc:
        return make_response(str(exc), 400)
    except subprocess.CalledProcessError as exc:
        return make_response(f"ffmpeg error: {exc}", 500)
    return send_file(
        BytesIO(data),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name="preview.mp3",
    )


@app.route("/api/add_notes", methods=["POST"])
def api_add_notes():
    try:
        payload = request.get_json(force=True)
    except Exception:
        return make_response("Invalid JSON body", 400)
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list):
        return make_response("`segments` 字段必须是数组", 400)

    created = 0
    updated = 0
    ensure_listening_model()

    for item in segments:
        if not isinstance(item, dict):
            continue
        transcript = (item.get("transcript") or "").strip()
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            return make_response("起止时间格式不正确", 400)
        if end <= start:
            return make_response("结束时间必须大于开始时间", 400)
        if start < 0 or end > AUDIO_DURATION + 0.5:
            return make_response("时间范围超出音频长度", 400)

        if not transcript:
            with SEGMENT_LOCK:
                seg_obj = next(
                    (s for s in SEGMENTS if str(s.ident) == str(item.get("id"))),
                    None,
                )
            if seg_obj and seg_obj.transcript:
                transcript = seg_obj.transcript

        numeric_id, db_id, guid = build_note_identifiers(start, end)
        media_name = clip_audio(AUDIO_PATH, start, end, sanitize_audio_name(AUDIO_PATH.name, start, end, numeric_id))
        fields = {
            "Prompt": DEFAULT_PROMPT,
            "Audio": f"[sound:{media_name}]",
            "Transcript": transcript,
            "Source": f"音频：{AUDIO_PATH.name} {format_seconds_label(start)}-{format_seconds_label(end)}",
            "Stats": "",
            "DbId": db_id,
        }
        result = add_or_update_note(
            fields=fields,
            deck=DECK_NAME,
            model=LISTENING_MODEL,
            tags=DEFAULT_TAGS,
            guid=guid,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1

    return jsonify({"created": created, "updated": updated})


# sanitize_audio_name is needed from import_clb9 but not exported; reimplement here to match behavior.
def sanitize_audio_name(file_name: str, start: float, end: float, note_id: int) -> str:
    base = Path(file_name).stem.replace(" ", "_").replace(":", "").replace("/", "_")
    return f"clb9_listening_{note_id}_{base}_{format_timestamp(start)}-{format_timestamp(end)}.mp3"


def transcribe_cache_path(audio_path: Path) -> Path:
    digest = hashlib.md5(str(audio_path).encode("utf-8")).hexdigest()
    cache_dir = CURRENT_DIR / "data" / "listen_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.pkl"


def load_audio_file(audio_path: Path, *, use_cache: bool = True) -> None:
    global AUDIO_PATH, SEGMENTS, AUDIO_DURATION, TRANSCRIPTION_NOTICE, AUDIO_LOADED

    resolved = audio_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)

    try:
        ensure_listening_model()
    except AnkiError as exc:
        print(f"初始化 Anki 模板失败：{exc}")

    try:
        audio_duration = ffprobe_duration(resolved)
    except Exception as exc:
        raise RuntimeError(f"无法读取音频时长：{exc}") from exc

    try:
        segments = detect_segments(resolved, audio_duration)
    except subprocess.CalledProcessError as exc:
        err_text = exc.stderr.decode("utf-8", errors="ignore") if getattr(exc, "stderr", None) else str(exc)
        raise RuntimeError(f"静音检测失败，请确认已安装 ffmpeg。错误：{err_text}") from exc

    notice = ""
    transcript_chunks: List[Dict[str, Any]] = []
    if whisper is None:
        notice = "未安装 whisper 库，文本需手动填写。"
    else:
        try:
            cache_path = transcribe_cache_path(resolved)
            if use_cache and cache_path.exists():
                with cache_path.open("rb") as fh:
                    transcript_chunks = pickle.load(fh)
                notice = f"从缓存加载 Whisper ({WHISPER_MODEL_NAME}) 转写结果。"
            else:
                transcript_chunks = transcribe_audio_segments(resolved)
                with cache_path.open("wb") as fh:
                    pickle.dump(transcript_chunks, fh)
                notice = f"已使用 Whisper ({WHISPER_MODEL_NAME}) 自动生成文本，请核对。"
            attach_transcripts(segments, transcript_chunks)
            if not transcript_chunks:
                notice = "Whisper 未识别到有效文本，字段暂留空。"
        except Exception as exc:
            notice = f"自动转写失败：{exc}"

    with SEGMENT_LOCK:
        AUDIO_PATH = resolved
        AUDIO_DURATION = audio_duration
        SEGMENTS = segments
        TRANSCRIPTION_NOTICE = notice
        AUDIO_LOADED = True

    print(f"解析完成：共识别 {len(segments)} 个候选句子。音频：{resolved}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLB9 listening segment picker")
    parser.add_argument("--audio", type=Path, default=Path("/Users/fenglin/Desktop/code/english_listening_material/IELTS_LISTENING/ielts_11_2_1.mp3"), help="要处理的音频文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=5050, help="监听端口")
    parser.add_argument("--no-cache", action="store_true", help="忽略现有 Whisper 缓存并重新转写")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    try:
        load_audio_file(args.audio, use_cache=not args.no_cache)
    except FileNotFoundError as exc:
        missing = getattr(exc, "filename", str(args.audio))
        raise SystemExit(f"找不到音频文件：{missing}") from None
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"打开 http://{args.host}:{args.port} 开始筛选。")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已退出。")
