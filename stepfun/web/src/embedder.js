/**
 * 端侧 embedding：模型全部在浏览器里跑，不调用任何云端推理接口。
 *
 * - 优先使用 WebGPU；浏览器不支持或申请适配器失败时自动降级到 WASM；
 * - 支持 fp32 / fp16 / q8(int8) 三种精度，便于对比体积、速度与检索质量；
 * - 批量向量化，向上层汇报进度。
 */
import { pipeline, env } from "@huggingface/transformers";

/** 开源 embedding 模型（公开可获取），中文检索效果与体积的平衡点。 */
export const MODEL_ID = "Xenova/bge-small-zh-v1.5";
export const MODEL_REVISION = "main";
export const MODEL_DIMENSION = 512;

/**
 * 可切换的模型目录（均为公开开源权重、带官方 ONNX，支持 FP32/FP16/INT8）。
 * 题面允许自选开源 embedding 模型；换成别的模型只需改目录项，
 * 集合维度会随之变化，服务端会自动按新维度重建集合。
 */
export const MODEL_CATALOG = [
  { id: "Xenova/bge-small-zh-v1.5", dimension: 512, label: "BAAI bge-small-zh-v1.5（中文，默认）" },
  { id: "Xenova/all-MiniLM-L6-v2", dimension: 384, label: "all-MiniLM-L6-v2（多语言备选）" },
  { id: "Xenova/paraphrase-multilingual-MiniLM-L12-v2", dimension: 384, label: "paraphrase-multilingual-MiniLM-L12-v2（多语言备选）" },
];

/** 池化方式：bge 系列官方推荐 CLS + 归一化。 */
const POOLING = "cls";

/**
 * onnxruntime-web 默认从 CDN 取 wasm 运行时，这里改成本地 public/ort/，
 * 保证推理运行时也不依赖任何外部网络。
 */
env.backends.onnx.wasm.wasmPaths = "/ort/";

/** 模型本地目录（由 scripts/download-model.mjs 预下载）。 */
env.allowLocalModels = true;
env.localModelPath = "/models/";
env.useBrowserCache = false;

/** 探测本地是否已有模型文件：有则只用本地，没有才从远端（镜像）拉。 */
async function hasLocalModel(modelId) {
  try {
    const response = await fetch(`/models/${modelId}/config.json`, { method: "HEAD" });
    return response.ok;
  } catch {
    return false;
  }
}

export const DTYPE_LABEL = {
  fp32: "FP32（未量化基线）",
  fp16: "FP16",
  q8: "INT8",
  int8: "INT8",
};

/** 探测可用后端：优先 WebGPU，失败则 WASM。 */
export async function detectBackend() {
  if (typeof navigator !== "undefined" && navigator.gpu) {
    try {
      const adapter = await navigator.gpu.requestAdapter();
      if (adapter) {
        const info = adapter.info ?? (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
        return {
          backend: "webgpu",
          adapter: info ? `${info.vendor ?? ""} ${info.architecture ?? ""} ${info.description ?? ""}`.trim() : "WebGPU adapter",
        };
      }
    } catch (error) {
      console.warn("[embedder] WebGPU 探测失败，降级到 WASM:", error);
    }
  }
  return { backend: "wasm", adapter: "WASM（多线程）" };
}

/** 加载模型（本地优先，缺失时从远端镜像拉取）。 */
export async function createEmbedder({ dtype = "q8", device = null, onProgress, model = MODEL_ID } = {}) {
  let detected = device ? { backend: device, adapter: device } : await detectBackend();
  const entry = MODEL_CATALOG.find((item) => item.id === model) ?? { id: model, dimension: MODEL_DIMENSION };

  // 请求 WebGPU 但本机没有 WebGPU 适配器时，如实降级到 WASM（界面显示真实后端）
  if (detected.backend === "webgpu") {
    const probe = await detectBackend();
    if (probe.backend !== "webgpu") {
      detected = { backend: "wasm", adapter: "WASM（请求 WebGPU 但本机无 WebGPU 适配器，已降级）" };
    }
  }

  const local = await hasLocalModel(entry.id);
  if (!local) {
    // huggingface.co 直连不通时走镜像；推理仍发生在浏览器本地
    env.remoteHost = "https://hf-mirror.com/";
  }

  const extractor = await pipeline("feature-extraction", entry.id, {
    dtype,
    device: detected.backend,
    revision: MODEL_REVISION,
    local_files_only: local,
    progress_callback: (progress) => {
      if (progress?.status === "progress" && progress.total) {
        onProgress?.({ status: "download", file: progress.file, loaded: progress.loaded, total: progress.total });
      } else if (progress?.status) {
        onProgress?.({ status: progress.status, file: progress.file });
      }
    },
  });

  return {
    model: entry.id,
    revision: MODEL_REVISION,
    dtype,
    dimension: entry.dimension,
    backend: detected.backend,
    adapter: detected.adapter,

    /** 批量生成向量，返回 number[][]（已 L2 归一化）。 */
    async embed(texts, { batchSize = 8, onBatch } = {}) {
      const vectors = [];
      for (let index = 0; index < texts.length; index += batchSize) {
        const batch = texts.slice(index, index + batchSize);
        const output = await extractor(batch, { pooling: POOLING, normalize: true });
        const rows = output.tolist();
        for (const row of rows) vectors.push(row);
        onBatch?.(Math.min(index + batchSize, texts.length), texts.length);
        // 让出主线程，避免批量推理时界面卡死
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      return vectors;
    },
  };
}
