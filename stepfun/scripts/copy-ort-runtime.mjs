/**
 * 把 onnxruntime-web 的 wasm 运行时复制到 web/public/ort/，
 * 让端侧推理完全走本地资源，不从 CDN 下载任何运行时文件。
 *
 * 用法：node scripts/copy-ort-runtime.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..", "web");
const SOURCE = path.join(WEB, "node_modules", "onnxruntime-web", "dist");
const TARGET = path.join(WEB, "public", "ort");

if (!fs.existsSync(SOURCE)) {
  console.error(`[ort] 找不到 onnxruntime-web 产物：${SOURCE}（先执行 npm --prefix web install）`);
  process.exit(1);
}

fs.mkdirSync(TARGET, { recursive: true });
const patterns = [/\.wasm$/, /\.mjs$/];
const copied = [];
for (const name of fs.readdirSync(SOURCE)) {
  if (!patterns.some((pattern) => pattern.test(name))) continue;
  if (name.endsWith(".map")) continue;
  fs.copyFileSync(path.join(SOURCE, name), path.join(TARGET, name));
  copied.push(name);
}
console.log(`[ort] 已复制 ${copied.length} 个运行时文件到 ${TARGET}`);
console.log(`[ort] ${copied.join(", ")}`);
