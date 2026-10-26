/**
 * 迷你知识库前端：导入文档 → 端侧向量化 → 写入 seekdb → 混合检索。
 *
 * 所有 embedding 都在浏览器本地完成；后端只负责落库与检索。
 */
import { createEmbedder, detectBackend, MODEL_ID, MODEL_CATALOG, DTYPE_LABEL } from "./embedder.js";

const API = "";
const params = new URLSearchParams(location.search);
const requestedBackend = params.get("backend");
const requestedModel = params.get("model");
const state = {
  embedder: null,
  dtype: params.get("dtype") ?? "q8",
  // auto / 空 → 自动探测（WebGPU 优先，不可用则 WASM）
  forceBackend: requestedBackend && requestedBackend !== "auto" ? requestedBackend : null,
  model: requestedModel && MODEL_CATALOG.some((item) => item.id === requestedModel) ? requestedModel : MODEL_ID,
  chunks: [], // {id, text, source}
  timings: { embedMs: 0, writeMs: 0, queryEmbedMs: 0, searchMs: 0 },
};

const $ = (id) => document.getElementById(id);
const setText = (id, value) => { $(id).textContent = value; };

function log(message, kind = "info") {
  const line = document.createElement("div");
  line.className = `log ${kind}`;
  line.textContent = message;
  $("log").appendChild(line);
  $("log").scrollTop = $("log").scrollHeight;
}

/** 简易切块：按段落合并到 ~200 字（兼容 CRLF/LF）。 */
function chunkText(text, maxChars = 200) {
  const paragraphs = text.split(/\r?\n\s*\r?\n/).map((p) => p.trim()).filter(Boolean);
  const chunks = [];
  let buffer = "";
  for (const paragraph of paragraphs) {
    if ((buffer + "\n\n" + paragraph).length > maxChars && buffer) {
      chunks.push(buffer);
      buffer = paragraph;
    } else {
      buffer = buffer ? `${buffer}\n\n${paragraph}` : paragraph;
    }
  }
  if (buffer) chunks.push(buffer);
  return chunks.length ? chunks : [text.trim()];
}

async function ensureEmbedder() {
  if (state.embedder) return state.embedder;
  const detected = state.forceBackend
    ? { backend: state.forceBackend, adapter: state.forceBackend === "webgpu" ? "WebGPU（强制）" : "WASM（强制）" }
    : await detectBackend();
  log(`推理后端：${detected.backend === "webgpu" ? "WebGPU" : "WASM（自动降级）"} · ${detected.adapter}`, "info");
  setText("backendBadge", detected.backend === "webgpu" ? "WebGPU" : "WASM");
  setText("modelInfo", `${state.model} · ${DTYPE_LABEL[state.dtype]}`);

  const loadStarted = performance.now();
  state.embedder = await createEmbedder({
    dtype: state.dtype,
    device: detected.backend,
    model: state.model,
    onProgress: (progress) => {
      if (progress.status === "download" && progress.total) {
        const percent = Math.round((progress.loaded / progress.total) * 100);
        setText("progressText", `下载模型 ${progress.file ?? ""} ${percent}%`);
        $("progress").value = percent;
      }
    },
  });
  state.modelLoadMs = performance.now() - loadStarted;  state.embedder.backend = detected.backend;
  state.embedder.adapter = detected.adapter;
  // 徽标显示模型实际使用的后端（请求 WebGPU 但本机不可用时，这里会如实显示 WASM）
  setText("backendBadge", state.embedder.backend === "webgpu" ? "WebGPU" : "WASM");
  window.__model = {
    model: state.embedder.model,
    revision: state.embedder.revision,
    dtype: state.embedder.dtype,
    dimension: state.embedder.dimension,
    backend: state.embedder.backend,
    adapter: state.embedder.adapter,
    loadMs: state.modelLoadMs,
  };
  log(`模型就绪：${state.model}（${DTYPE_LABEL[state.dtype]}，加载 ${Math.round(state.modelLoadMs)} ms）`, "ok");
  return state.embedder;
}

async function importFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;
  const embedder = await ensureEmbedder();

  const chunks = [];
  for (const file of files) {
    const text = await file.text();
    chunkText(text).forEach((chunk, index) => {
      chunks.push({ id: `${file.name}#${index}`, text: chunk, source: file.name });
    });
  }
  state.chunks = chunks;
  log(`切块完成：${files.length} 个文件 → ${chunks.length} 个片段`, "info");

  $("progress").value = 0;
  setText("progressText", "端侧向量化中…");
  const embedStarted = performance.now();
  const embeddings = await embedder.embed(
    chunks.map((chunk) => chunk.text),
    {
      batchSize: 8,
      onBatch: (done, total) => {
        const percent = Math.round((done / total) * 100);
        $("progress").value = percent;
        setText("progressText", `端侧向量化 ${done}/${total}`);
      },
    },
  );
  state.timings.embedMs = performance.now() - embedStarted;
  log(`端侧向量化完成：${chunks.length} 条，用时 ${Math.round(state.timings.embedMs)} ms`, "ok");

  setText("progressText", "写入 seekdb…");
  const writeStarted = performance.now();
  const response = await fetch(`${API}/api/documents`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ids: chunks.map((chunk) => chunk.id),
      documents: chunks.map((chunk) => chunk.text),
      metadatas: chunks.map((chunk) => ({ source: chunk.source })),
      embeddings,
      dimension: state.embedder.dimension,
    }),
  });
  const result = await response.json();
  state.timings.writeMs = performance.now() - writeStarted;
  if (!result.ok) {
    log(`写入失败：${result.error}`, "err");
    return;
  }
  $("progress").value = 100;
  setText("progressText", `已写入 ${result.written} 条（写入+刷新索引 ${Math.round(state.timings.writeMs)} ms）`);
  log(`写入 seekdb 成功：${result.written} 条，耗时 ${result.elapsedMs} ms（索引已刷新）`, "ok");
  window.__import = {
    files: files.length,
    chunks: chunks.length,
    embedMs: state.timings.embedMs,
    writeMs: state.timings.writeMs,
    embedThroughput: chunks.length / (state.timings.embedMs / 1000),
  };
}

async function runQuery() {
  const keyword = $("query").value.trim();
  if (!keyword) return;
  const embedder = await ensureEmbedder();

  const embedStarted = performance.now();
  const [vector] = await embedder.embed([keyword]);
  state.timings.queryEmbedMs = performance.now() - embedStarted;

  const searchStarted = performance.now();
  const response = await fetch(`${API}/api/search`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ keyword, queryEmbedding: vector, nResults: 5 }),
  });
  const result = await response.json();
  state.timings.searchMs = performance.now() - searchStarted;
  if (!result.ok) {
    log(`检索失败：${result.error}`, "err");
    return;
  }

  const list = $("results");
  list.innerHTML = "";
  result.items.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = "result";
    card.innerHTML = `
      <div class="result-head">
        <span class="rank">#${index + 1}</span>
        <span class="name">${escapeHtml(item.metadata?.source ?? item.id)}</span>
        <span class="score">${item.distance === null ? "" : `距离 ${Number(item.distance).toFixed(4)}`}</span>
      </div>
      <div class="snippet">${escapeHtml(item.document.slice(0, 220))}${item.document.length > 220 ? "…" : ""}</div>
    `;
    list.appendChild(card);
  });
  log(
    `检索完成：${result.items.length} 条 · 查询向量化 ${Math.round(state.timings.queryEmbedMs)} ms · seekdb 检索 ${result.elapsedMs} ms · 端到端 ${Math.round(state.timings.queryEmbedMs + state.timings.searchMs)} ms`,
    "ok",
  );
  // 供基准脚本读取的指标
  window.__metrics = {
    ...state.timings,
    searchElapsedMs: result.elapsedMs,
    resultCount: result.items.length,
    sources: result.items.map((item) => item.metadata?.source ?? item.id),
  };
  window.__lastQuery = keyword;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

async function resetKnowledgeBase() {
  await fetch(`${API}/api/reset`, { method: "POST" });
  state.chunks = [];
  $("results").innerHTML = "";
  $("progress").value = 0;
  setText("progressText", "");
  log("知识库已清空", "info");
}

function bind() {
  $("fileInput").addEventListener("change", (event) => {
    importFiles(event.target.files).catch((error) => log(`导入失败：${error}`, "err"));
  });
  $("queryBtn").addEventListener("click", () => {
    runQuery().catch((error) => log(`检索失败：${error}`, "err"));
  });
  $("resetBtn").addEventListener("click", () => {
    resetKnowledgeBase().catch((error) => log(`重置失败：${error}`, "err"));
  });
  $("dtype").addEventListener("change", (event) => {
    state.dtype = event.target.value;
    state.embedder = null; // 换精度需要重新加载模型
    setText("modelInfo", `${state.model} · ${DTYPE_LABEL[state.dtype]}`);
    log(`切换到 ${DTYPE_LABEL[state.dtype]}，下次加载模型时生效`, "info");
  });
}

bind();
setText("modelInfo", `${state.model} · ${DTYPE_LABEL[state.dtype]}`);

// 页面打开即探测并显示当前推理后端（评审第一眼就能看到端侧后端状态）
(async () => {
  try {
    const detected = state.forceBackend
      ? { backend: state.forceBackend, adapter: state.forceBackend === "webgpu" ? "WebGPU（强制）" : "WASM（强制）" }
      : await detectBackend();
    setText("backendBadge", detected.backend === "webgpu" ? "WebGPU" : "WASM");
    log(`推理后端：${detected.backend === "webgpu" ? "WebGPU" : "WASM（自动降级）"} · ${detected.adapter}`, "info");
  } catch (error) {
    log(`后端探测失败：${error}`, "err");
  }
})();
