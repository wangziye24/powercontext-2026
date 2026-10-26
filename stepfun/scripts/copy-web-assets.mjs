/**
 * 把前端依赖的浏览器产物复制到 web/public/vendor/。
 *
 * 这样前端不需要打包器：页面用 import map 直接加载本地 ESM，
 * 推理运行时（onnxruntime-web 的 wasm）也从本地加载，不依赖任何 CDN。
 *
 * 用法：node scripts/copy-web-assets.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..", "web");
const NM = path.join(WEB, "node_modules");
const VENDOR = path.join(WEB, "public", "vendor");

function copyFile(from, to) {
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.copyFileSync(from, to);
  return path.relative(VENDOR, to);
}

const copied = [];

// 1) transformers.js 的浏览器 ESM 产物
const transformers = path.join(NM, "@huggingface", "transformers", "dist", "transformers.web.js");
if (!fs.existsSync(transformers)) {
  console.error("[vendor] 缺少 transformers.web.js，请先执行 npm --prefix web install");
  process.exit(1);
}
copied.push(copyFile(transformers, path.join(VENDOR, "transformers.web.js")));

// 2) onnxruntime-web 的浏览器 ESM 与 wasm 运行时
const ortDist = path.join(NM, "onnxruntime-web", "dist");
for (const name of fs.readdirSync(ortDist)) {
  if (!/\.(mjs|wasm)$/.test(name)) continue;
  copied.push(copyFile(path.join(ortDist, name), path.join(VENDOR, "ort", name)));
}

// 3) onnxruntime-common 的 ESM 产物（transformers.web.js 的依赖）
const commonDist = path.join(NM, "onnxruntime-common", "dist", "esm");
function copyTree(from, to) {
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const src = path.join(from, entry.name);
    const dst = path.join(to, entry.name);
    if (entry.isDirectory()) copyTree(src, dst);
    else if (entry.name.endsWith(".js")) copied.push(copyFile(src, dst));
  }
}
if (fs.existsSync(commonDist)) copyTree(commonDist, path.join(VENDOR, "ort-common"));

console.log(`[vendor] 已复制 ${copied.length} 个文件到 ${VENDOR}`);
console.log("[vendor] import map 需要的入口：");
console.log("  @huggingface/transformers -> /vendor/transformers.web.js");
console.log("  onnxruntime-web            -> /vendor/ort/ort.webgpu.bundle.min.mjs");
console.log("  onnxruntime-common         -> /vendor/ort-common/index.js");
