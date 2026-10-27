/**
 * 迷你知识库后端：静态托管前端 + 接收端侧算好的向量并落到本地 seekdb。
 *
 * 运行：node server/src/server.mjs
 * 端口：默认 5178（可用 PORT 覆盖）
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startLocalSeekdb } from "./local-seekdb.mjs";
import { createClient, ensureCollection, addDocuments, hybridSearch, resetCollection, ensureDimension } from "./seekdb-store.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(HERE, "../../web");       // 前端源码（零构建，浏览器直接跑 ESM）
const WEB_PUBLIC = path.join(WEB_ROOT, "public");     // vendor 运行时与 ort wasm
const DATA_DIR = path.resolve(HERE, "../../data");
const PORT = Number(process.env.PORT ?? 5178);
const COLLECTION = process.env.SEEKDB_COLLECTION ?? "knowledge_base";
const DIMENSION = Number(process.env.SEEKDB_DIMENSION ?? 512);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  });
  res.end(payload);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
}

function serveStatic(req, res, urlPath) {
  const rel = urlPath === "/" ? "/index.html" : urlPath;
  const clean = path.normalize(rel).replace(/^([/\\])+/, "");
  // /vendor/*、/ort/* 来自 public/，其余来自 web 根目录
  const candidates = clean.startsWith("vendor/") || clean.startsWith("ort/")
    ? [path.join(WEB_PUBLIC, clean)]
    : [path.join(WEB_ROOT, clean), path.join(WEB_PUBLIC, clean)];

  for (const filePath of candidates) {
    if (!filePath.startsWith(WEB_ROOT) && !filePath.startsWith(WEB_PUBLIC)) continue;
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) continue;
    const stat = fs.statSync(filePath);
    const headers = {
      "content-type": MIME[path.extname(filePath)] ?? "application/octet-stream",
      "content-length": String(stat.size),
      "cache-control": "no-cache",
    };
    if (req.method === "HEAD") {
      // HEAD 探测（前端用它判断模型是否已本地化）必须只回头不回体
      res.writeHead(200, headers);
      res.end();
      return;
    }
    res.writeHead(200, headers);
    fs.createReadStream(filePath).pipe(res);
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("not found");
}

async function main() {
  console.log(`[server] 启动本地 seekdb（数据目录 ${DATA_DIR}）...`);
  const db = await startLocalSeekdb({
    dataDir: DATA_DIR,
    log: (message) => console.log(message),
  });
  const endpoint = { host: db.host, port: db.port, user: db.user };
  console.log("[server] seekdb endpoint:", JSON.stringify(endpoint));

  const client = createClient(endpoint);
  let collection = (await ensureCollection(client, { name: COLLECTION, dimension: DIMENSION })).collection;
  console.log(`[server] 集合 ${COLLECTION} 就绪（维度 ${DIMENSION}）`);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const { pathname } = url;

    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "access-control-allow-origin": "*",
        "access-control-allow-headers": "content-type",
        "access-control-allow-methods": "GET,POST,OPTIONS",
      });
      res.end();
      return;
    }

    try {
      if (pathname === "/api/health") {
        sendJson(res, 200, {
          ok: true,
          endpoint: { host: endpoint.host, port: endpoint.port },
          collection: COLLECTION,
          dimension: DIMENSION,
        });
        return;
      }

      if (pathname === "/api/documents" && req.method === "POST") {
        const payload = await readBody(req);
        const dimension = Number(payload.dimension ?? DIMENSION);
        // 换了模型导致维度变化时，按新维度重建集合
        if (dimension && collection.dimension !== dimension) {
          console.log(`[server] 集合维度 ${collection.dimension} → ${dimension}，重建集合`);
          collection = (await ensureDimension(client, { name: COLLECTION, dimension })).collection;
        }
        const started = Date.now();
        await addDocuments(collection, payload);
        sendJson(res, 200, { ok: true, written: payload.ids.length, elapsedMs: Date.now() - started });
        return;
      }

      if (pathname === "/api/search" && req.method === "POST") {
        const payload = await readBody(req);
        if (!Array.isArray(payload.queryEmbedding)) {
          sendJson(res, 400, { ok: false, error: "缺少 queryEmbedding" });
          return;
        }
        const started = Date.now();
        const result = await hybridSearch({
          collection,
          keyword: payload.keyword ?? "",
          queryEmbedding: payload.queryEmbedding,
          nResults: Number(payload.nResults ?? 5),
        });
        sendJson(res, 200, { ok: true, ...result, elapsedMs: Date.now() - started });
        return;
      }

      if (pathname === "/api/reset" && req.method === "POST") {
        collection = (await resetCollection(client, { name: COLLECTION, dimension: DIMENSION })).collection;
        sendJson(res, 200, { ok: true });
        return;
      }

      if (pathname === "/api/stats") {
        // 集合当前行数（用于确认清空/增量写入是否符合预期）
        const tables = await client.execute("SHOW TABLES");
        const names = tables.map((row) => Object.values(row)[0]).filter(Boolean);
        const stats = [];
        for (const name of names) {
          if (name === "sdk_collections") continue;
          try {
            const rows = await client.execute(`SELECT COUNT(*) AS n FROM \`${name}\``);
            stats.push({ table: name, rows: Number(Object.values(rows[0] ?? {})[0] ?? 0) });
          } catch {
            /* 非集合表忽略 */
          }
        }
        sendJson(res, 200, { ok: true, stats });
        return;
      }

      if (pathname === "/api/schema") {
        // 供技术说明引用：真实的表结构、索引与行数
        const tables = await client.execute("SHOW TABLES");
        const names = tables.map((row) => Object.values(row)[0]).filter(Boolean);
        const ddl = [];
        for (const name of names) {
          try {
            const rows = await client.execute(`SHOW CREATE TABLE \`${name}\``);
            ddl.push({ table: name, create: Object.values(rows[0] ?? {})[1] ?? "" });
          } catch (error) {
            ddl.push({ table: name, create: `（无法读取 DDL: ${error.message}）` });
          }
        }
        sendJson(res, 200, { ok: true, tables: names, ddl });
        return;
      }

      serveStatic(req, res, pathname);
    } catch (error) {
      console.error("[server] 处理失败:", error);
      sendJson(res, 500, { ok: false, error: String(error?.message ?? error) });
    }
  });

  server.listen(PORT, "127.0.0.1", () => {
    console.log(`[server] 应用地址 http://127.0.0.1:${PORT}`);
  });

  const shutdown = () => {
    console.log("\n[server] 关闭中...");
    server.close();
    try {
      client.close?.();
    } catch {
      /* 忽略关闭错误 */
    }
    Promise.resolve(db.stop()).finally(() => process.exit(0));
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((error) => {
  console.error("[server] 启动失败:", error);
  process.exit(1);
});
