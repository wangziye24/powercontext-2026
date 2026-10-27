/**
 * 预下载端侧 embedding 模型到 web/public/models/（运行时不再依赖任何外网）。
 *
 * 默认从 huggingface.co 取；不可达时自动回落到 hf-mirror.com。
 * 同时写出 manifest.json，记录每个文件的字节数与 sha256，
 * 供技术说明里的"模型体积/压缩比"使用（同一口径）。
 *
 * 用法：node scripts/download-model.mjs [--model Xenova/bge-small-zh-v1.5]
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..", "web");
const MODELS_ROOT = path.join(WEB, "public", "models");

const argModel = process.argv.includes("--model")
  ? process.argv[process.argv.indexOf("--model") + 1]
  : "Xenova/bge-small-zh-v1.5";

/** 需要的文件：配置 + 分词器 + 三种精度的 ONNX 权重。 */
const FILES = [
  ["config.json", "模型配置"],
  ["tokenizer.json", "分词器"],
  ["tokenizer_config.json", "分词器配置"],
  ["special_tokens_map.json", "特殊 token"],
  ["onnx/model.onnx", "权重 FP32"],
  ["onnx/model_fp16.onnx", "权重 FP16"],
  ["onnx/model_quantized.onnx", "权重 INT8(q8)"],
];

const HOSTS = [process.env.HF_ENDPOINT ?? "https://huggingface.co/", "https://hf-mirror.com/"];

async function head(url) {
  try {
    const response = await fetch(url, { method: "HEAD", redirect: "follow" });
    return response.ok ? response : null;
  } catch {
    return null;
  }
}

async function pickHost(model) {
  for (const host of HOSTS) {
    const url = `${host}${model}/resolve/main/config.json`;
    process.stdout.write(`[model] 探测 ${host} ... `);
    if (await head(url)) {
      console.log("可用");
      return host;
    }
    console.log("不可用");
  }
  throw new Error("所有模型源都不可达，请设置 HF_ENDPOINT 环境变量");
}

async function download(url, target) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) throw new Error(`下载失败 ${url}: HTTP ${response.status}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, buffer);
  return buffer;
}

async function main() {
  const model = argModel;
  const revision = "main";
  const host = await pickHost(model);
  // transformers.js 的本地模型路径是 /models/<model>/…（不带 revision 目录）
  const base = path.join(MODELS_ROOT, model);
  fs.mkdirSync(base, { recursive: true });

  const downloadStarted = Date.now();
  const manifest = { model, revision, source: host, files: [], downloadedAt: new Date().toISOString() };
  for (const [file, label] of FILES) {
    const target = path.join(base, file);
    if (fs.existsSync(target) && fs.statSync(target).size > 0) {
      const buffer = fs.readFileSync(target);
      console.log(`[model] 已存在 ${file}（${(buffer.length / 1048576).toFixed(2)} MiB）`);
      manifest.files.push({ file, label, bytes: buffer.length, sha256: crypto.createHash("sha256").update(buffer).digest("hex") });
      continue;
    }
    const url = `${host}${model}/resolve/${revision}/${file}`;
    process.stdout.write(`[model] 下载 ${file} ... `);
    const buffer = await download(url, target);
    console.log(`${(buffer.length / 1048576).toFixed(2)} MiB`);
    manifest.files.push({ file, label, bytes: buffer.length, sha256: crypto.createHash("sha256").update(buffer).digest("hex") });
  }

  const byLabel = Object.fromEntries(manifest.files.map((item) => [item.label, item.bytes]));
  const total = (labels) => labels.reduce((sum, label) => sum + (byLabel[label] ?? 0), 0);
  const common = ["分词器", "分词器配置", "模型配置", "特殊 token"];
  manifest.totals = {
    fp32: total([...common, "权重 FP32"]),
    fp16: total([...common, "权重 FP16"]),
    q8: total([...common, "权重 INT8(q8)"]),
  };
  manifest.compression = {
    fp32ToFp16: manifest.totals.fp32 / manifest.totals.fp16,
    fp32ToInt8: manifest.totals.fp32 / manifest.totals.q8,
  };

  manifest.downloadSeconds = Number(((Date.now() - downloadStarted) / 1000).toFixed(2));
  manifest.downloadedBytes = manifest.files.reduce((sum, item) => sum + item.bytes, 0);
  fs.writeFileSync(path.join(base, "manifest.json"), JSON.stringify(manifest, null, 2), "utf-8");
  console.log(`[model] 完成 → ${base}`);
  console.log(
    `[model] 下载耗时 ${manifest.downloadSeconds}s，共 ${(manifest.downloadedBytes / 1048576).toFixed(2)} MiB（运行时从本地读取，无需再下载）`,
  );
  console.log(
    `[model] 体积：FP32 ${(manifest.totals.fp32 / 1048576).toFixed(2)} MiB · ` +
      `FP16 ${(manifest.totals.fp16 / 1048576).toFixed(2)} MiB · ` +
      `INT8 ${(manifest.totals.q8 / 1048576).toFixed(2)} MiB`,
  );
  console.log(
    `[model] 压缩比：FP32→FP16 ${manifest.compression.fp32ToFp16.toFixed(2)}× · ` +
      `FP32→INT8 ${manifest.compression.fp32ToInt8.toFixed(2)}×`,
  );
}

main().catch((error) => {
  console.error("[model] 失败:", error.message);
  process.exit(1);
});
