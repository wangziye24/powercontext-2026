import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    target: "esnext", // WebGPU / WASM 需要较新的语法
    sourcemap: false,
  },
  optimizeDeps: {
    exclude: ["@huggingface/transformers"], // 避免预打包破坏 onnxruntime-web 的 wasm 资源
  },
  server: {
    port: 5179,
    proxy: {
      "/api": "http://127.0.0.1:5178",
    },
  },
});
