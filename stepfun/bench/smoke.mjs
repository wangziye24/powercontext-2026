/**
 * 端到端冒烟测试：驱动系统 Edge 跑一遍「导入 → 端侧向量化 → 写 seekdb → 混合检索」。
 *
 * 用法：node bench/smoke.mjs [--dtype q8] [--backend wasm]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const APP = process.env.APP_URL ?? "http://127.0.0.1:5178";
const EDGE = process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
};
const dtype = arg("dtype", "q8");
const backend = arg("backend", "wasm");

const corpusFiles = fs
  .readdirSync(path.join(ROOT, "corpus"))
  .filter((name) => name.endsWith(".md"))
  .map((name) => path.join(ROOT, "corpus", name));

const browser = await chromium.launch({
  executablePath: EDGE,
  headless: true,
  args: backend === "webgpu" ? ["--enable-unsafe-webgpu", "--enable-features=Vulkan,WebGPU"] : [],
});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(`console: ${message.text()}`);
});

console.log(`[smoke] 打开 ${APP}/?dtype=${dtype}&backend=${backend}`);
await page.goto(`${APP}/?dtype=${dtype}&backend=${backend}`, { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => document.getElementById("backendBadge")?.textContent !== "检测中…", null, { timeout: 60000 });
console.log("[smoke] 后端徽标:", await page.textContent("#backendBadge"));

// 清空知识库，保证每次冒烟从同一状态开始
await page.evaluate(() => fetch("/api/reset", { method: "POST" }));

console.log(`[smoke] 导入 ${corpusFiles.length} 个语料文件…`);
await page.setInputFiles("#fileInput", corpusFiles);
await page.waitForFunction(() => window.__import !== undefined, null, { timeout: 900000 });
const importInfo = await page.evaluate(() => window.__import);
const modelInfo = await page.evaluate(() => window.__model);
console.log("[smoke] 模型:", JSON.stringify(modelInfo));
console.log("[smoke] 导入:", JSON.stringify(importInfo));

console.log("[smoke] 检索测试…");
await page.fill("#query", "混合检索为什么要由数据库完成融合排序");
await page.click("#queryBtn");
await page.waitForFunction(() => window.__metrics !== undefined, null, { timeout: 120000 });
const metrics = await page.evaluate(() => window.__metrics);
console.log("[smoke] 指标:", JSON.stringify(metrics));

const hits = await page.$$eval(".result .name", (nodes) => nodes.map((node) => node.textContent));
console.log("[smoke] 结果来源:", JSON.stringify(hits));

await browser.close();
if (errors.length) {
  console.log("[smoke] 页面错误:");
  for (const error of errors.slice(0, 10)) console.log("   ", error.slice(0, 300));
}
console.log(hits.length ? "SMOKE OK" : "SMOKE FAILED（没有检索结果）");
process.exit(hits.length ? 0 : 1);
