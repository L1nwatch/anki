const nextBtn = document.getElementById("next-btn");
const revealBtn = document.getElementById("reveal-btn");
const statusEl = document.getElementById("status");
const promptEl = document.getElementById("prompt");
const audioWrapper = document.getElementById("audio-wrapper");
const audioPlayer = document.getElementById("audio-player");
const listenInputWrapper = document.getElementById("listen-input-wrapper");
const listenInput = document.getElementById("listen-input");
const wordInputWrapper = document.getElementById("word-input-wrapper");
const wordInput = document.getElementById("word-input");
const writingOriginal = document.getElementById("writing-original");
const writingOriginalContent = writingOriginal.querySelector(".content");
const answerBox = document.getElementById("answer-box");
const easeButtons = document.getElementById("ease-buttons");

let currentCard = null;
let answerShown = false;

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

function renderTokens(tokens) {
  if (!tokens || !tokens.length) {
    return "";
  }
  const parts = [];
  tokens.forEach((token, idx) => {
    const cls = token.status === "match" ? "diff-match" : token.status === "missing" ? "diff-miss" : "diff-extra";
    const html = `<span class="${cls}">${escapeHtml(token.text)}</span>`;
    const prev = tokens[idx - 1];
    const isPunct = /^[,.;!?):\]”’]$/.test(token.text);
    const prevIsOpening = prev ? /^[({“‘]$/.test(prev.text) : false;
    if (idx > 0 && !isPunct && !prevIsOpening) {
      parts.push(" ");
    }
    parts.push(html);
  });
  return parts.join("");
}

async function fetchDiff(expected, actual) {
  const res = await fetch("/api/diff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected, actual }),
  });
  if (!res.ok) {
    throw new Error("diff 计算失败");
  }
  return res.json();
}

function clearUI() {
  currentCard = null;
  answerShown = false;
  promptEl.innerHTML = "";
  writingOriginal.classList.add("hidden");
  audioWrapper.classList.add("hidden");
  answerBox.classList.add("hidden");
  answerBox.innerHTML = "";
  easeButtons.classList.add("hidden");
  easeButtons.innerHTML = "";
  listenInputWrapper.classList.add("hidden");
  listenInput.value = "";
  wordInputWrapper.classList.add("hidden");
  wordInput.value = "";
  revealBtn.disabled = true;
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

function renderCard(payload) {
  const { type, data, questionHtml } = payload;
  if (data.prompt) {
    promptEl.textContent = data.prompt;
  } else if (questionHtml) {
    promptEl.textContent = questionHtml.replace(/\[anki:play:[^\]]+\]/g, "");
  } else {
    promptEl.textContent = "";
  }

  if (type === "listening") {
    listenInputWrapper.classList.remove("hidden");
    listenInput.focus();
    writingOriginal.classList.add("hidden");
  } else if (type === "word") {
    wordInputWrapper.classList.remove("hidden");
    wordInput.focus();
    writingOriginal.classList.add("hidden");
  } else if (type === "writing") {
    writingOriginal.classList.remove("hidden");
    writingOriginalContent.textContent = data.original || "";
  } else {
    writingOriginal.classList.add("hidden");
  }

  if (data.audioUrl) {
    audioWrapper.classList.remove("hidden");
    audioPlayer.src = data.audioUrl;
    audioPlayer.currentTime = 0;
    setTimeout(() => {
      audioPlayer.currentTime = 0;
      audioPlayer.play().catch(() => {});
    }, 200);
  } else {
    audioWrapper.classList.add("hidden");
    audioPlayer.removeAttribute("src");
  }

  easeButtons.classList.add("hidden");
  easeButtons.innerHTML = "";
}

async function translateToChinese(text) {
  if (!text) return "";
  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      throw new Error("翻译失败");
    }
    const payload = await res.json();
    return payload.translation || "";
  } catch (err) {
    console.warn("translate error", err);
    return "";
  }
}

async function showAnswer() {
  if (!currentCard || answerShown) {
    return;
  }
  answerShown = true;
  try {
    await fetch("/api/reveal", { method: "POST" });
  } catch (err) {
    console.warn("reveal failed", err);
  }
  const { type, data, answerHtml } = currentCard;
  let html = "";

  if (type === "listening" || type === "word") {
    const expectedText = type === "listening" ? data.transcript : data.word;
    const inputBox = type === "listening" ? listenInput : wordInput;
    const userText = inputBox.value.trim();
    try {
      const diff = await fetchDiff(expectedText, userText);
      const expectedHtml = diff.expected?.length
        ? renderTokens(diff.expected)
        : `<em>${escapeHtml(expectedText || "")}</em>`;
      const actualHtml = userText
        ? diff.actual?.length
          ? renderTokens(diff.actual)
          : escapeHtml(userText)
        : "<em>（未填写）</em>";
      const translation = expectedText
        ? `<div class="translation-block" data-text="${escapeHtml(expectedText)}">
            <button type="button" class="translate-btn">显示中文翻译</button>
            <div class="translation-text hidden"></div>
          </div>`
        : "";
      html = `
        <div><strong>${type === "listening" ? "标准答案" : "正确拼写"}：</strong></div>
        <div class="diff-line">${expectedHtml}</div>
        ${type === "listening" && data.source ? `<div class="section-title">音频信息</div><div>${escapeHtml(data.source)}</div>` : ""}
        <div class="section-title">你的输入</div>
        <div class="diff-line">${actualHtml}</div>
        ${translation}
      `;
    } catch (err) {
      html = `
        <div><strong>标准答案：</strong></div>
        <div class="diff-line">${escapeHtml(expectedText || "")}</div>
        ${type === "listening" && data.source ? `<div class="section-title">音频信息</div><div>${escapeHtml(data.source)}</div>` : ""}
        <div class="section-title">你的输入</div>
        <div class="diff-line">${userText ? escapeHtml(userText) : "<em>（未填写）</em>"}</div>
      `;
    }
  } else if (type === "writing") {
    html = `
      <div><strong>修改建议：</strong></div>
      <div>${escapeHtml(data.corrected)}</div>
    `;
  } else {
    html = answerHtml || "<em>无答案</em>";
  }

  answerBox.innerHTML = html;
  answerBox.classList.remove("hidden");

  answerBox.querySelectorAll(".translate-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const container = btn.closest(".translation-block");
      const text = container?.dataset.text || "";
      btn.disabled = true;
      btn.textContent = "翻译中…";
      const translation = await translateToChinese(text);
      const output = container.querySelector(".translation-text");
      if (output) {
        output.textContent = translation || "(暂无翻译)";
        output.classList.remove("hidden");
      }
      btn.remove();
    });
  });
}

async function loadCard() {
  clearUI();
  statusEl.textContent = "加载中…";
  nextBtn.disabled = true;

  try {
    const res = await fetch("/api/next");
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
    statusEl.textContent = `当前卡片：${payload.type.toUpperCase()} ｜ Card ${payload.cardId} ｜ 待复习 ${counts.due ?? 0} ｜ 学习 ${counts.learning ?? 0} ｜ 新卡 ${counts.new ?? 0}`;
    revealBtn.disabled = false;
  } catch (err) {
    statusEl.textContent = `获取卡片失败：${err.message}`;
  } finally {
    nextBtn.disabled = false;
  }
}

async function submitAnswer(ease) {
  if (!currentCard) {
    return;
  }
  statusEl.textContent = "提交中…";
  easeButtons.querySelectorAll("button").forEach((btn) => (btn.disabled = true));
  revealBtn.disabled = true;
  nextBtn.disabled = true;

  try {
    const res = await fetch("/api/answer", {
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
    easeButtons.querySelectorAll("button").forEach((btn) => (btn.disabled = false));
    revealBtn.disabled = false;
    nextBtn.disabled = false;
  }
}

function init() {
  nextBtn.addEventListener("click", loadCard);
  revealBtn.addEventListener("click", async () => {
    revealBtn.disabled = true;
    await showAnswer();
    if (currentCard && Array.isArray(currentCard.buttons)) {
      renderEaseButtons(currentCard.buttons);
    }
  });
  loadCard();
}

document.addEventListener("DOMContentLoaded", init);
