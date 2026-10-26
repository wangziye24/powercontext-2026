/**
 * 端侧知识库基准与检索质量评测。
 *
 * 用 Playwright 驱动本机 Edge（不下载 Chromium，直接用系统 Edge），
 * 对每个组合（精度 × 推理后端）执行：
 *   导入语料 → 端侧向量化 → 写入 seekdb → 逐条查询 → 记录耗时与命中
 * 并统计 Recall@5 / nDCG@5，以及模型文件体积（量化前后）与压缩比。
 *
 * 用法：node bench/run-bench.mjs [--app http://127.0.0.1:5178]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const APP = process.env.APP_URL ?? "http://127.0.0.1:5178";
const EDGE = process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const REPEATS = Number(process.env.BENCH_REPEATS ?? 3);
const K = 5;

/**
 * 模型体积：直接统计本地模型文件（同一口径），并读取 manifest 里的 sha256。
 * 不依赖外网——模型已由 scripts/download-model.mjs 预下载到 web/public/models。
 */
const MODEL_ID = "Xenova/bge-small-zh-v1.5";

async function measureModelSizes() {
  const modelDir = path.join(ROOT, "web", "public", "models", MODEL_ID);
  const manifestPath = path.join(modelDir, "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`找不到模型清单 ${manifestPath}，请先执行 node scripts/download-model.mjs`);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  const result = {};
  for (const [dtype, labels] of Object.entries({
    fp32: ["权重 FP32", "分词器", "分词器配置", "模型配置", "特殊 token"],
    fp16: ["权重 FP16", "分词器", "分词器配置", "模型配置", "特殊 token"],
    q8: ["权重 INT8(q8)", "分词器", "分词器配置", "模型配置", "特殊 token"],
  })) {
    const files = manifest.files.filter((item) => labels.includes(item.label));
    const totalBytes = files.reduce((sum, item) => sum + item.bytes, 0);
    // 以磁盘实际大小复核
    const onDisk = files.reduce((sum, item) => {
      const file = path.join(modelDir, item.file);
      return sum + (fs.existsSync(file) ? fs.statSync(file).size : 0);
    }, 0);
    result[dtype] = { totalBytes: onDisk || totalBytes, manifestBytes: totalBytes, detail: files };
  }
  return result;
}

const median = (values) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/**
 * Recall@K 与 nDCG@K（标注集见 corpus/queries.json）。
 * 约定：同一文档的多个片段只计一次命中（先出现者计分），避免召回率大于 1。
 */
function score(sources, relevant) {
  const credited = new Set();
  let dcg = 0;
  sources.slice(0, K).forEach((source, index) => {
    if (relevant.includes(source) && !credited.has(source)) {
      credited.add(source);
      dcg += 1 / Math.log2(index + 2);
    }
  });
  const recall = relevant.length ? credited.size / relevant.length : 0;
  const idealCount = Math.min(relevant.length, K);
  let idcg = 0;
  for (let index = 0; index < idealCount; index += 1) idcg += 1 / Math.log2(index + 2);
  return { recall, ndcg: idcg ? dcg / idcg : 0, hitCount: credited.size };
}

async function runCase({ dtype, backend, queries, corpusFiles }) {
  const args = ["--no-sandbox"];
  if (backend === "webgpu") args.push("--enable-unsafe-webgpu", "--enable-features=Vulkan,WebGPU");
  const browser = await chromium.launch({ executablePath: EDGE, headless: true, args });
  const context = await browser.newContext();
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(String(error)));

  await page.goto(`${APP}/?dtype=${dtype}&backend=${backend}`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.getElementById("backendBadge")?.textContent !== "检测中…");
  // 每轮从干净状态开始，保证不同精度/后端之间可比
  await page.evaluate(() => fetch("/api/reset", { method: "POST" }));

  // 导入语料（端侧向量化 + 写入 seekdb）
  await page.setInputFiles("#fileInput", corpusFiles);
  await page.waitForFunction(() => window.__import !== undefined, null, { timeout: 600000 });
  const importInfo = await page.evaluate(() => window.__import);
  const modelInfo = await page.evaluate(() => window.__model);

  // 逐条查询（重复 REPEATS 次，取中位数；第一次视为冷启动）
  const perQuery = [];
  for (const item of queries) {
    const samples = [];
    for (let round = 0; round < REPEATS; round += 1) {
      await page.fill("#query", item.q);
      await page.click("#queryBtn");
      await page.waitForFunction((q) => window.__lastQuery === q, item.q, { timeout: 120000 });
      const metrics = await page.evaluate(() => window.__metrics);
      samples.push(metrics);
    }
    const cold = samples[0];
    const warm = samples.slice(1);
    const quality = score(cold.sources, item.relevant);
    perQuery.push({
      query: item.q,
      relevant: item.relevant,
      cold: { queryEmbedMs: cold.queryEmbedMs, searchMs: cold.searchElapsedMs, endToEndMs: cold.queryEmbedMs + cold.searchElapsedMs },
      warmMedian: {
        queryEmbedMs: median(warm.map((s) => s.queryEmbedMs)),
        searchMs: median(warm.map((s) => s.searchElapsedMs)),
        endToEndMs: median(warm.map((s) => s.queryEmbedMs + s.searchElapsedMs)),
      },
      sources: cold.sources,
      ...quality,
    });
  }

  await browser.close();
  return {
    dtype,
    requestedBackend: backend,
    backend: modelInfo.backend, // 实际使用的后端（无 WebGPU 适配器时会如实是 wasm）
    adapter: modelInfo.adapter,
    model: modelInfo,
    import: importInfo,
    queries: perQuery,
    consoleErrors,
  };
}

function summarize(result) {
  const recalls = result.queries.map((q) => q.recall);
  const ndcgs = result.queries.map((q) => q.ndcg);
  return {
    recallAtK: recalls.reduce((a, b) => a + b, 0) / recalls.length,
    ndcgAtK: ndcgs.reduce((a, b) => a + b, 0) / ndcgs.length,
    coldEndToEndMs: median(result.queries.map((q) => q.cold.endToEndMs)),
    warmEndToEndMs: median(result.queries.map((q) => q.warmMedian.endToEndMs)),
    warmQueryEmbedMs: median(result.queries.map((q) => q.warmMedian.queryEmbedMs)),
    warmSearchMs: median(result.queries.map((q) => q.warmMedian.searchMs)),
  };
}

async function main() {
  const outDir = path.join(ROOT, "bench", "results");
  fs.mkdirSync(outDir, { recursive: true });

  const queries = JSON.parse(fs.readFileSync(path.join(ROOT, "corpus", "queries.json"), "utf-8"));
  const corpusDir = path.join(ROOT, "corpus");
  const corpusFiles = fs
    .readdirSync(corpusDir)
    .filter((name) => name.endsWith(".md"))
    .map((name) => path.join(corpusDir, name));

  console.log("[bench] 统计模型文件体积…");
  const modelSizes = await measureModelSizes();
  for (const [dtype, info] of Object.entries(modelSizes)) {
    console.log(`  ${dtype}: ${(info.totalBytes / 1048576).toFixed(2)} MiB`);
  }
  const compression = {
    fp32ToFp16: modelSizes.fp32.totalBytes / modelSizes.fp16.totalBytes,
    fp32ToInt8: modelSizes.fp32.totalBytes / modelSizes.q8.totalBytes,
  };

  const cases = [];
  const plan = [
    { dtype: "fp32", backend: "auto" },
    { dtype: "fp32", backend: "wasm" }, // 同精度下对比 WASM 与 WebGPU
    { dtype: "fp16", backend: "auto" },
    { dtype: "q8", backend: "auto" },
    { dtype: "q8", backend: "wasm" },
  ];
  for (const { dtype, backend } of plan) {
    console.log(`[bench] 运行 ${dtype} / ${backend === "auto" ? "自动探测" : "强制 WASM"} ...`);
    const result = await runCase({ dtype, backend, queries, corpusFiles });
    result.summary = summarize(result);
    cases.push(result);
    console.log(
      `  实际后端=${result.backend} Recall@${K}=${result.summary.recallAtK.toFixed(3)} ` +
        `nDCG@${K}=${result.summary.ndcgAtK.toFixed(3)} 端到端(热)=${Math.round(result.summary.warmEndToEndMs)}ms ` +
        `向量化吞吐=${result.import.embedThroughput.toFixed(2)} chunk/s`,
    );
  }

  const payload = {
    generatedAt: new Date().toISOString(),
    app: APP,
    browser: "Microsoft Edge (系统自带, Playwright 驱动)",
    k: K,
    repeats: REPEATS,
    modelFiles: modelSizes,
    compression,
    cases,
  };
  fs.writeFileSync(path.join(outDir, "bench.json"), JSON.stringify(payload, null, 2), "utf-8");

  // Markdown 汇总
  const rows = cases.map((item) => {
    const s = item.summary;
    return `| ${item.dtype} | ${item.backend} | ${(modelSizes[item.dtype].totalBytes / 1048576).toFixed(2)} | ` +
      `${s.recallAtK.toFixed(3)} | ${s.ndcgAtK.toFixed(3)} | ${item.import.embedThroughput.toFixed(2)} | ` +
      `${Math.round(s.warmQueryEmbedMs)} | ${Math.round(s.warmSearchMs)} | ${Math.round(s.warmEndToEndMs)} |`;
  });
  const webgpuAvailable = cases.some((item) => item.backend === "webgpu");
  const markdown = [
    `# 端侧知识库基准结果（生成于 ${payload.generatedAt}）`,
    "",
    `浏览器：${payload.browser}；K=${K}；每条查询重复 ${REPEATS} 次取中位数（首次为冷启动）。`,
    `语料：${corpusFiles.length} 篇文档 / ${cases[0]?.import.chunks ?? "?"} 个片段 / ${queries.length} 条标注查询。`,
    "",
    webgpuAvailable
      ? "本机提供 WebGPU 适配器，上表中的 webgpu 行即 WebGPU 实测。"
      : "**本机 Edge 未提供 WebGPU 适配器（`navigator.gpu` 缺失，多组启动参数均无效），因此所有实测均为 WASM 后端；" +
        "应用按题面要求自动降级到 WASM 并在界面如实提示。WebGPU 路径已实现（显式 `device: 'webgpu'` + 适配器探测），" +
        "在具备 WebGPU 的机器上可用 `?backend=webgpu` 复现。",
    "",
    "| 精度 | 实际后端 | 模型总体积(MiB) | Recall@5 | nDCG@5 | 向量化吞吐(chunk/s) | 查询向量化(ms) | seekdb 检索(ms) | 端到端(ms) |",
    "|---|---|---|---|---|---|---|---|---|",
    ...rows,
    "",
    `压缩比：FP32→FP16 = ${compression.fp32ToFp16.toFixed(2)}×；FP32→INT8 = ${compression.fp32ToInt8.toFixed(2)}×。`,
    "",
  ].join("\n");
  fs.writeFileSync(path.join(outDir, "bench.md"), markdown, "utf-8");
  console.log(`[bench] 结果已写入 ${outDir}`);
}

main().catch((error) => {
  console.error("[bench] 失败:", error);
  process.exit(1);
});
