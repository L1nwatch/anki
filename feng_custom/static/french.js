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

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

let currentCard = null;
let answerShown = false;
let mediaRecorder = null;
let mediaStream = null;
let recordedChunks = [];
let playbackUrl = null;
let recognition = null;
let recognitionShouldRestart = false;
let recognitionActive = false;
let recognizedSegments = [];

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

function normalizeForCompare(value) {
  return (value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-zœæç'\-]+/g, " ")
    .trim();
}

function levenshtein(a, b) {
  const m = a.length;
  const n = b.length;
  if (!m) return n;
  if (!n) return m;
  const dp = new Array(n + 1);
  for (let j = 0; j <= n; j += 1) {
    dp[j] = j;
  }
  for (let i = 1; i <= m; i += 1) {
    let prev = dp[0];
    dp[0] = i;
    for (let j = 1; j <= n; j += 1) {
      const temp = dp[j];
      if (a[i - 1] === b[j - 1]) {
        dp[j] = prev;
      } else {
        dp[j] = Math.min(prev + 1, dp[j] + 1, dp[j - 1] + 1);
      }
      prev = temp;
    }
  }
  return dp[n];
}

function similarityScore(expected, actual) {
  const cleanExpected = normalizeForCompare(expected);
  const cleanActual = normalizeForCompare(actual);
  if (!cleanExpected || !cleanActual) {
    return 0;
  }
  const distance = levenshtein(cleanExpected, cleanActual);
  const maxLen = Math.max(cleanExpected.length, cleanActual.length);
  if (!maxLen) {
    return 0;
  }
  return 1 - distance / maxLen;
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
  stopRecognition();
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
  recognizedSegments = [];
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

function initRecognition() {
  if (!SpeechRecognition || recognition) {
    return;
  }
  recognition = new SpeechRecognition();
  recognition.lang = "fr-FR";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.continuous = true;
  recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (result.isFinal && result[0]) {
        recognizedSegments.push(result[0].transcript.trim());
      }
    }
  };
  recognition.onerror = (event) => {
    if (event.error === "no-speech") {
      return;
    }
    recordingStatus.textContent = `语音识别错误：${event.error}`;
  };
  recognition.onstart = () => {
    recognitionActive = true;
  };
  recognition.onend = () => {
    recognitionActive = false;
    if (recognitionShouldRestart && mediaRecorder && mediaRecorder.state === "recording") {
      try {
        recognition.start();
      } catch (err) {
        recordingStatus.textContent = `无法继续识别：${err.message}`;
      }
    }
  };
}

function startRecognition() {
  if (!SpeechRecognition) {
    return;
  }
  initRecognition();
  if (!recognition) {
    return;
  }
  recognizedSegments = [];
  recognitionShouldRestart = true;
  try {
    recognition.start();
  } catch (err) {
    recordingStatus.textContent = `语音识别未启动：${err.message}`;
  }
}

function stopRecognition() {
  recognitionShouldRestart = false;
  if (recognition && recognitionActive) {
    try {
      recognition.stop();
    } catch (err) {}
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
    startRecognition();
  } catch (err) {
    recordBtn.disabled = false;
    recordingStatus.textContent = `无法访问麦克风：${err.message}`;
  }
}

function stopRecording() {
  stopActiveRecording();
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
  recordingStatus.textContent = SpeechRecognition
    ? "点击“开始录音”进行跟读练习。"
    : "浏览器不支持语音识别，但仍可录音回放。";
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
  recordingStatus.textContent = SpeechRecognition
    ? "点击“开始录音”进行跟读练习。"
    : "浏览器不支持语音识别，但仍可录音回放。";
}

async function evaluatePronunciation() {
  if (!currentCard) {
    return;
  }

  if (!SpeechRecognition) {
    feedbackBox.textContent = "当前浏览器不支持语音识别，请通过回放手动判断发音。";
    feedbackBox.classList.remove("hidden");
    transcriptBox.classList.add("hidden");
    transcriptBox.innerHTML = "";
    return;
  }

  const expected = currentCard.data?.sentence || "";
  const actual = recognizedSegments.join(" ").trim();
  if (actual) {
    transcriptBox.innerHTML = `<strong>语音识别：</strong> ${escapeHtml(actual)}`;
    transcriptBox.classList.remove("hidden");
  } else {
    transcriptBox.innerHTML = "语音识别未返回结果，可能需要更明确的发音。";
    transcriptBox.classList.remove("hidden");
  }
  let feedback = "未识别到有效文本，请再试一次。";
  if (actual) {
    const score = similarityScore(expected, actual);
    const pct = Math.round(score * 100);
    if (score >= 0.85) {
      feedback = `匹配度 ${pct}%：发音非常接近，继续保持！`;
    } else if (score >= 0.6) {
      feedback = `匹配度 ${pct}%：不错，可以再注意重音或连读。`;
    } else {
      feedback = `匹配度 ${pct}%：识别偏差较大，建议重新听原音后再试。`;
    }
  }
  feedbackBox.textContent = feedback;
  feedbackBox.classList.remove("hidden");
}

async function showAnswer() {
  if (!currentCard || answerShown) {
    return;
  }
  answerShown = true;
  stopRecording();
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
  await evaluatePronunciation();
  revealBtn.classList.add("hidden");
  if (currentCard && Array.isArray(currentCard.buttons)) {
    renderEaseButtons(currentCard.buttons);
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
    statusEl.textContent = `卡 FR ｜ 待 ${counts.due ?? 0} ｜ 学 ${counts.learning ?? 0} ｜ 新 ${counts.new ?? 0}`;
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
  if (!SpeechRecognition) {
    recordingStatus.textContent = "浏览器不支持语音识别，将无法自动评分。";
  }
  loadCard();
}

document.addEventListener("DOMContentLoaded", init);
