/**
 * 启动前自举：把浏览器端第三方产物与端侧模型准备齐。
 *
 * 仓库里不提交这些大文件（vendor 运行时与 ONNX 权重），
 * 任何自动评测器或新环境只需要 `npm install && npm start` 就能跑起来。
 *
 * 用法：node scripts/prepare.mjs [--force]
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..", "web");
const MODEL_ID = "Xenova/bge-small-zh-v1.5";

const force = process.argv.includes("--force");

function log(message) {
  console.log(`[prepare] ${message}`);
}

/** 1) 浏览器端第三方 ESM / wasm 运行时（已随仓库提供，缺失时才重新生成） */
const vendorMarker = path.join(WEB, "public", "vendor", "transformers.web.js");
if (force || !fs.existsSync(vendorMarker)) {
  log("浏览器端第三方产物缺失，重新生成（需要先 npm --prefix web install）…");
  const result = spawnSync(process.execPath, [path.join(HERE, "copy-web-assets.mjs")], { stdio: "inherit" });
  if (result.status !== 0) {
    console.error("[prepare] 拷贝失败：请先执行 npm --prefix web install");
    process.exit(result.status ?? 1);
  }
} else {
  log("浏览器端第三方产物已就绪（随仓库提供，无需安装）");
}

/** 2) 端侧 embedding 模型（公开权重，INT8/FP16/FP32 三种精度） */
const modelDir = path.join(WEB, "public", "models", MODEL_ID);
const modelFile = path.join(modelDir, "onnx", "model_quantized.onnx");
if (force || !fs.existsSync(modelFile)) {
  log("下载端侧模型（首次约 160 MiB，直连不通时自动回落 hf-mirror.com）…");
  const result = spawnSync(process.execPath, [path.join(HERE, "download-model.mjs")], { stdio: "inherit" });
  if (result.status !== 0) {
    console.warn("[prepare] 模型下载失败：应用仍可启动，但端侧推理需要在浏览器里联网取模型");
  }
} else {
  log("端侧模型已就绪");
}

log("准备完成");
