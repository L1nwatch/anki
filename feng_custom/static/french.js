const statusEl = document.getElementById("status");
const sentenceEl = document.getElementById("sentence-text");
const promptAudioWrapper = document.getElementById("prompt-audio-wrapper");
const promptAudio = document.getElementById("prompt-audio");
const recordBtn = document.getElementById("record-btn");
const revealBtn = document.getElementById("reveal-btn");
const recordingStatus = document.getElementById("recording-status");
const playbackAudio = document.getElementById("playback-audio");
const answerBox = document.getElementById("answer-box");
const meaningEl = document.getElementById("english-meaning");
const exampleBlock = document.getElementById("example-block");
const exampleFrEl = document.getElementById("example-fr");
const exampleEnEl = document.getElementById("example-en");
const transcriptBox = document.getElementById("transcript-box");
const feedbackBox = document.getElementById("feedback-box");
const easeButtons = document.getElementById("ease-buttons");

const statusUserSuffix = (() => {
  if (!statusEl) return "";
  const name = statusEl.dataset.username;
  return name ? ` (user: ${name})` : "";
})();

let currentCard = null;
let answerShown = false;
let mediaRecorder = null;
let mediaStream = null;
let recordedChunks = [];
let playbackUrl = null;
let recordedBlob = null;

function buildRecordedBlobIfNeeded() {
  if (recordedBlob || !recordedChunks.length) {
    return recordedBlob;
  }
  recordedBlob = new Blob(recordedChunks, { type: "audio/webm" });
  return recordedBlob;
}

const easeLabels = {
  1: "再来",
  2: "较难",
  3: "良好",
  4: "容易",
};

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function stopActiveRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    try {
      mediaRecorder.stop();
    } catch (err) {}
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  recordBtn.textContent = "开始录音";
  recordBtn.classList.remove("recording");
  recordBtn.disabled = false;
  recordingStatus.classList.remove("recording");
  if (!answerShown) {
    recordingStatus.textContent = "录音已结束，可点击“显示答案”。";
  }
}

function resetRecordingArtifacts(resetStatus = true) {
  stopActiveRecording();
  recordedChunks = [];
  recordedBlob = null;
  if (playbackUrl) {
    URL.revokeObjectURL(playbackUrl);
    playbackUrl = null;
  }
  playbackAudio.classList.add("hidden");
  playbackAudio.removeAttribute("src");
  if (resetStatus) {
    recordingStatus.textContent = "";
  }
}

async function startRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    recordingStatus.textContent = "当前浏览器不支持录音。";
    return;
  }
  recordBtn.disabled = true;
  recordingStatus.textContent = "请求麦克风权限…";
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaStream = stream;
    recordedChunks = [];
    recordedBlob = null;
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => {
      if (event.data?.size) {
        recordedChunks.push(event.data);
      }
    };
    mediaRecorder.onstop = (event) => {
      if (!recordedChunks.length) {
        mediaRecorder = null;
        return;
      }
      const mimeType = event.target?.mimeType || "audio/webm";
      const blob = new Blob(recordedChunks, { type: mimeType });
      recordedBlob = blob;
      playbackUrl = URL.createObjectURL(blob);
      playbackAudio.src = playbackUrl;
      playbackAudio.classList.remove("hidden");
      mediaRecorder = null;
    };
    mediaRecorder.start();
    recordBtn.textContent = "停止录音";
    recordBtn.classList.add("recording");
    recordingStatus.textContent = "录音中… 影子跟读吧！";
    recordingStatus.classList.add("recording");
    recordBtn.disabled = false;
  } catch (err) {
    recordBtn.disabled = false;
    recordingStatus.textContent = `无法访问麦克风：${err.message}`;
  }
}

function stopRecording() {
  stopActiveRecording();
}

async function ensureRecordingStopped() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    await new Promise((resolve) => {
      mediaRecorder.addEventListener("stop", resolve, { once: true });
      try {
        mediaRecorder.stop();
      } catch (err) {
        resolve();
      }
    });
  }
}

function renderEaseButtons(buttons) {
  easeButtons.innerHTML = "";
  buttons.forEach((btn) => {
    const buttonEl = document.createElement("button");
    buttonEl.type = "button";
    buttonEl.textContent = easeLabels[btn] || `选项 ${btn}`;
    buttonEl.dataset.ease = btn;
    buttonEl.classList.add(`ease-${btn}`);
    buttonEl.addEventListener("click", () => submitAnswer(btn));
    easeButtons.appendChild(buttonEl);
  });
  easeButtons.classList.remove("hidden");
}

function clearUI() {
  currentCard = null;
  answerShown = false;
  sentenceEl.textContent = "";
  if (promptAudio && promptAudioWrapper) {
    try {
      promptAudio.pause();
    } catch (err) {}
    promptAudio.removeAttribute("src");
    promptAudioWrapper.classList.add("hidden");
  }
  answerBox.classList.add("hidden");
  meaningEl.textContent = "";
  exampleBlock.classList.add("hidden");
  exampleFrEl.textContent = "";
  exampleEnEl.textContent = "";
  transcriptBox.classList.add("hidden");
  transcriptBox.innerHTML = "";
  feedbackBox.classList.add("hidden");
  feedbackBox.textContent = "";
  easeButtons.classList.add("hidden");
  easeButtons.innerHTML = "";
  recordingStatus.textContent = "点击“开始录音”，完成后点击“显示答案”上传评分。";
  recordingStatus.classList.remove("recording");
  revealBtn.disabled = true;
  revealBtn.classList.remove("hidden");
  resetRecordingArtifacts(false);
}

function renderCard(payload) {
  const data = payload.data || {};
  sentenceEl.textContent = data.sentence || "";
  if (promptAudio && promptAudioWrapper) {
    try {
      promptAudio.pause();
    } catch (err) {}
    promptAudio.removeAttribute("src");
    promptAudioWrapper.classList.add("hidden");
  }
  recordingStatus.textContent = "点击“开始录音”，完成后点击“显示答案”上传评分。";
}

async function analyzePronunciation() {
  if (!currentCard) {
    return;
  }

  buildRecordedBlobIfNeeded();

  if (!recordedBlob) {
    transcriptBox.innerHTML = "未找到录音，请先录音后再评分。";
    transcriptBox.classList.remove("hidden");
    feedbackBox.textContent = "";
    feedbackBox.classList.add("hidden");
    return;
  }

  transcriptBox.innerHTML = "正在上传并分析录音…";
  transcriptBox.classList.remove("hidden");
  feedbackBox.classList.add("hidden");
  feedbackBox.textContent = "";

  const form = new FormData();
  form.append("cardId", currentCard.cardId);
  const uploadName = recordedBlob.type && recordedBlob.type.includes("mp4")
    ? "recording.m4a"
    : "recording.webm";
  form.append("audio", recordedBlob, uploadName);

  try {
    const res = await fetch("/api/fr/analyze", { method: "POST", body: form });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new Error(payload.error || `服务器返回 ${res.status}`);
    }
    const payload = await res.json();
    const transcript = payload.transcript || "";
    const feedback = payload.feedback || "";
    if (transcript) {
      transcriptBox.innerHTML = `<strong>转写：</strong> ${escapeHtml(transcript)}`;
    } else {
      transcriptBox.innerHTML = "未识别到有效文本。";
    }
    transcriptBox.classList.remove("hidden");
    if (feedback) {
      feedbackBox.textContent = feedback;
      feedbackBox.classList.remove("hidden");
    } else {
      feedbackBox.textContent = "";
      feedbackBox.classList.add("hidden");
    }
  } catch (err) {
    transcriptBox.innerHTML = `分析失败：${escapeHtml(err.message)}`;
    transcriptBox.classList.remove("hidden");
    feedbackBox.textContent = "";
    feedbackBox.classList.add("hidden");
  }
}

async function showAnswer() {
  if (!currentCard || answerShown) {
    return;
  }
  answerShown = true;
  stopRecording();
  await ensureRecordingStopped();
  try {
    await fetch("/api/fr/reveal", { method: "POST" });
  } catch (err) {
    console.warn("reveal failed", err);
  }
  const data = currentCard.data || {};
  answerBox.classList.remove("hidden");
  meaningEl.textContent = data.english || "暂无英文释义";
  if (data.sentence) {
    exampleBlock.classList.remove("hidden");
    exampleFrEl.textContent = data.sentence;
    exampleEnEl.textContent = data.english || "";
  } else {
    exampleBlock.classList.add("hidden");
    exampleFrEl.textContent = "";
    exampleEnEl.textContent = "";
  }
  const revealData = currentCard?.data || {};
  if (revealData.audioUrl && promptAudio && promptAudioWrapper) {
    promptAudioWrapper.classList.remove("hidden");
    promptAudio.src = revealData.audioUrl;
    promptAudio.load();
    try {
      promptAudio.currentTime = 0;
      promptAudio.play().catch(() => {});
    } catch (err) {}
  }
  await analyzePronunciation();
  revealBtn.classList.add("hidden");
  if (currentCard && Array.isArray(currentCard.buttons)) {
    renderEaseButtons(currentCard.buttons);
  }
}

async function loadCard() {
  clearUI();
  statusEl.textContent = "加载中…";
  try {
    const res = await fetch("/api/fr/next");
    if (!res.ok) {
      if (res.status === 404) {
        statusEl.textContent = "当前没有到期的卡片。";
        return;
      }
      throw new Error(`服务器返回 ${res.status}`);
    }
    const payload = await res.json();
    currentCard = payload;
    renderCard(payload);
    const counts = payload.counts || {};
    statusEl.textContent = `待 ${counts.due ?? 0} ｜ 学 ${counts.learning ?? 0} ｜ 新 ${counts.new ?? 0}${statusUserSuffix}`;
    revealBtn.disabled = false;
  } catch (err) {
    statusEl.textContent = `获取卡片失败：${err.message}`;
  }
}

async function submitAnswer(ease) {
  if (!currentCard) {
    return;
  }
  statusEl.textContent = "提交中…";
  easeButtons.querySelectorAll("button").forEach((btn) => {
    btn.disabled = true;
  });
  try {
    const res = await fetch("/api/fr/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cardId: currentCard.cardId, ease }),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new Error(payload.error || `服务器返回 ${res.status}`);
    }
    statusEl.textContent = "答案已提交，获取下一张…";
    await loadCard();
  } catch (err) {
    statusEl.textContent = `提交失败：${err.message}`;
    easeButtons.querySelectorAll("button").forEach((btn) => {
      btn.disabled = false;
    });
    revealBtn.disabled = false;
  }
}

function init() {
  recordBtn.addEventListener("click", () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      stopRecording();
    } else {
      startRecording();
    }
  });
  revealBtn.addEventListener("click", async () => {
    revealBtn.disabled = true;
    await showAnswer();
  });
  loadCard();
}

document.addEventListener("DOMContentLoaded", init);
