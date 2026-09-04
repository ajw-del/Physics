const EMBED_MODEL = "gemini-embedding-001";
const GEN_MODEL = "gemini-3.5-flash-lite";
const TOP_K = 6;
const API_KEY_STORAGE_KEY = "gemini_api_key";

let INDEX = [];

// ---------- 초기화 ----------
window.addEventListener("DOMContentLoaded", async () => {
  refreshApiKeyUI();
  await loadIndex();
});

async function loadIndex() {
  const countEl = document.getElementById("videoCount");
  try {
    const res = await fetch("data/index.json", { cache: "no-store" });
    if (!res.ok) throw new Error("index.json을 불러오지 못했습니다");
    const raw = await res.json();

    // embedding이 없거나 형식이 잘못된 항목은 걸러낸다 (검색 중 오류 방지)
    INDEX = raw.filter(
      (c) => Array.isArray(c.embedding) && c.embedding.length > 0
    );
    const skipped = raw.length - INDEX.length;
    if (skipped > 0) {
      console.warn(`embedding이 없는 청크 ${skipped}개를 건너뜀`);
    }

    const videoIds = new Set(INDEX.map((c) => c.video_id));
    countEl.textContent = `영상 ${videoIds.size}개, 청크 ${INDEX.length}개 로드됨`;
    if (skipped > 0) {
      countEl.textContent += ` (형식이 잘못된 ${skipped}개 청크는 제외됨)`;
    }
  } catch (e) {
    countEl.textContent = "인덱스를 불러오지 못했습니다. data/index.json이 있는지 확인하세요.";
    console.error(e);
  }
}

// ---------- API 키 관리 ----------
function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE_KEY) || "";
}

function saveApiKey() {
  const input = document.getElementById("apiKeyInput");
  const key = input.value.trim();
  if (!key) return;
  localStorage.setItem(API_KEY_STORAGE_KEY, key);
  input.value = "";
  refreshApiKeyUI();
}

function clearApiKey() {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  refreshApiKeyUI();
}

function refreshApiKeyUI() {
  const hasKey = !!getApiKey();
  document.getElementById("apiKeySection").classList.toggle("saved", hasKey);
  document.getElementById("keyStatus").classList.toggle("visible", hasKey);
}

// ---------- 임베딩 / 검색 ----------
async function embedText(text, apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${EMBED_MODEL}:embedContent`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
    body: JSON.stringify({ content: { parts: [{ text }] } }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`임베딩 요청 실패: ${res.status} ${err}`);
  }
  const data = await res.json();
  return data.embedding.values;
}

function cosineSimilarity(a, b) {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

function searchTopChunks(questionEmbedding) {
  const scored = INDEX.map((chunk) => ({
    chunk,
    score: cosineSimilarity(questionEmbedding, chunk.embedding),
  }));
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, TOP_K);
}

// ---------- 답변 생성 ----------
function buildPrompt(question, topChunks) {
  const context = topChunks
    .map(
      (item, i) =>
        `[출처 ${i + 1}: ${item.chunk.title}]\n${item.chunk.text}`
    )
    .join("\n\n");

  return `아래는 여러 유튜브 영상의 자막 발췌입니다. 질문에 답할 때 반드시 이 내용에만 근거하세요.
자막에 관련 내용이 전혀 없다면 다른 지식으로 추측하지 말고 answer 필드에 정확히 다음과 같이 답하세요:
"이 내용은 제공된 영상들에서 다루지 않았습니다."

관련 내용이 있다면, 어떤 출처(예: 출처 1, 출처 3)에서 나온 내용인지 답변에 자연스럽게 포함하세요.

그리고 아래 제공된 출처 발췌 ${topChunks.length}개 각각에 대해, 실제로 무슨 내용을 담고 있는지 20자 내외의 짧은 한 문장으로 요약하세요.
질문과 직접 관련이 없는 출처도 빠짐없이 전부 요약하세요.

반드시 아래 JSON 형식으로만 답하세요. 설명이나 코드블록 표시 없이 JSON 객체 하나만 출력하세요:
{
  "answer": "질문에 대한 답변 텍스트",
  "sources": [
    { "index": 1, "summary": "출처 1 발췌의 한 줄 요약" }
  ]
}
(sources 배열은 반드시 ${topChunks.length}개 항목을 순서대로 모두 포함해야 합니다)

[자막 발췌]
${context}

[질문]
${question}`;
}

async function generateAnswer(prompt, apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEN_MODEL}:generateContent`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { responseMimeType: "application/json" },
    }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`답변 생성 요청 실패: ${res.status} ${err}`);
  }
  const data = await res.json();
  const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) throw new Error("응답을 받지 못했습니다");
  return text;
}

// ---------- UI 흐름 ----------
async function handleAsk() {
  const apiKey = getApiKey();
  const question = document.getElementById("question").value.trim();
  const statusEl = document.getElementById("status");
  const askBtn = document.getElementById("askBtn");
  const answerCard = document.getElementById("answerCard");

  if (!apiKey) {
    statusEl.textContent = "먼저 위에 Gemini API 키를 입력하고 저장하세요.";
    return;
  }
  if (!question) {
    statusEl.textContent = "질문을 입력하세요.";
    return;
  }
  if (INDEX.length === 0) {
    statusEl.textContent = "아직 인덱스가 로드되지 않았습니다.";
    return;
  }

  askBtn.disabled = true;
  answerCard.style.display = "none";

  try {
    statusEl.textContent = "질문을 분석하는 중...";
    const questionEmbedding = await embedText(question, apiKey);

    statusEl.textContent = "관련 영상을 찾는 중...";
    const topChunks = searchTopChunks(questionEmbedding);

    statusEl.textContent = "답변을 만드는 중...";
    const prompt = buildPrompt(question, topChunks);
    const rawResponse = await generateAnswer(prompt, apiKey);

    let answer = rawResponse;
    const sourceSummaries = {};
    try {
      const parsed = JSON.parse(rawResponse);
      answer = parsed.answer || rawResponse;
      (parsed.sources || []).forEach((s) => {
        if (s && s.index != null) sourceSummaries[s.index] = s.summary || "";
      });
    } catch (e) {
      console.warn("요약 JSON 파싱 실패, 요약 없이 표시합니다:", e);
    }

    renderAnswer(answer, topChunks, sourceSummaries);
    statusEl.textContent = "";
  } catch (e) {
    console.error(e);
    statusEl.textContent = `오류가 발생했습니다: ${e.message}`;
  } finally {
    askBtn.disabled = false;
  }
}

function renderAnswer(answer, topChunks, sourceSummaries) {
  document.getElementById("answer").textContent = answer;

  const sourcesEl = document.getElementById("sources");
  sourcesEl.innerHTML = "";

  const seen = new Set();
  topChunks.forEach(({ chunk }, i) => {
    const key = `${chunk.video_id}_${Math.floor(chunk.start)}`;
    if (seen.has(key)) return;
    seen.add(key);

    const seconds = Math.floor(chunk.start || 0);
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    const timeLabel = `${minutes}:${String(secs).padStart(2, "0")}`;
    const link = `${chunk.url}${chunk.url.includes("?") ? "&" : "?"}t=${seconds}s`;
    const summary = (sourceSummaries && sourceSummaries[i + 1]) || "";

    const a = document.createElement("a");
    a.href = link;
    a.target = "_blank";
    a.rel = "noopener";
    a.className = "source-item";
    a.innerHTML =
      `<div class="video-title">${chunk.title}</div>` +
      (summary ? `<div class="source-summary">${summary}</div>` : "") +
      `<div class="timestamp">${timeLabel} 지점 보기</div>`;
    sourcesEl.appendChild(a);
  });

  document.getElementById("answerCard").style.display = "block";
}
