/**
 * 本地 seekdb 服务进程管理。
 *
 * 官方绑定 @oceanbase/seekdb-node 在两种情况下起不来，这里自行拉起原生 seekdb.exe：
 *  1. 原生运行时内置于 node_modules，而仓库/工作目录若是中文等非 ASCII 路径，
 *     seekdb 把自身 copy/hardlink 到数据目录时会失败（err=5 拒绝访问）；
 *  2. Windows 上不会自动创建 etc/ log/ run/ store/ 子目录。
 * 因此这里把原生运行时装到纯 ASCII 目录、预建子目录、自选空闲端口再启动。
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);

/** 各平台对应的原生运行时包。 */
const NATIVE_PACKAGES = {
  "win32-x64": "@oceanbase/seekdb-node-win32-x64",
  "linux-x64": "@oceanbase/seekdb-node-linux-x64-gnu",
  "linux-arm64": "@oceanbase/seekdb-node-linux-arm64-gnu",
  "darwin-arm64": "@oceanbase/seekdb-node-darwin-arm64",
};

const isAscii = (value) => /^[\x20-\x7e]*$/.test(value);

/** 在临时目录里准备一份纯 ASCII 的原生运行时（按版本缓存，只复制一次）。 */
export function prepareNativeRuntime() {
  const key = `${process.platform}-${process.arch}`;
  const pkg = NATIVE_PACKAGES[key];
  if (!pkg) throw new Error(`本地 seekdb 不支持当前平台: ${key}`);

  const pkgJson = require.resolve(`${pkg}/package.json`);
  const version = require(`${pkg}/package.json`).version;
  const source = path.join(path.dirname(pkgJson), "native");
  if (!fs.existsSync(source)) throw new Error(`找不到原生运行时目录: ${source}`);
  if (isAscii(source)) return { nativeDir: source, exe: path.join(source, exeName()) };

  const cacheRoot = path.join(os.tmpdir(), "stepfun-kb");
  const nativeDir = path.join(cacheRoot, `native-${key}-${version}`);
  const marker = path.join(nativeDir, ".ready");
  if (!fs.existsSync(marker)) {
    fs.rmSync(nativeDir, { recursive: true, force: true });
    fs.mkdirSync(nativeDir, { recursive: true });
    fs.cpSync(source, nativeDir, { recursive: true });
    fs.writeFileSync(marker, new Date().toISOString());
  }
  return { nativeDir, exe: path.join(nativeDir, exeName()) };
}

const exeName = () => (process.platform === "win32" ? "seekdb.exe" : "seekdb");

/** 找一个空闲端口。 */
export function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** 等待端口可连接。 */
function waitForPort(port, timeoutMs, onTick) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.connect({ host: "127.0.0.1", port }, () => {
        socket.destroy();
        resolve();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`等待 seekdb 端口 ${port} 超时（${timeoutMs}ms）`));
          return;
        }
        onTick?.(Date.now() - started);
        setTimeout(attempt, 500);
      });
    };
    attempt();
  });
}

function tailLog(baseDir, lines = 30) {
  const logFile = path.join(baseDir, "log", "seekdb.log");
  if (!fs.existsSync(logFile)) return `（没有日志文件 ${logFile}）`;
  const content = fs.readFileSync(logFile, "utf-8").trimEnd().split("\n");
  return content.slice(-lines).join("\n");
}

/**
 * 启动本地 seekdb，返回 { host, port, user, stop() }。
 * @param {{dataDir?: string, timeoutMs?: number, log?: (msg: string) => void}} options
 */
export async function startLocalSeekdb({ dataDir, timeoutMs = 120000, log = console.log } = {}) {
  const { exe, nativeDir } = prepareNativeRuntime();

  // 数据目录同样必须可被原生进程访问：非 ASCII 时改用 LOCALAPPDATA/tmp。
  let baseDir = dataDir ?? path.resolve(process.cwd(), "data");
  if (!isAscii(baseDir)) {
    const fallback = path.join(os.tmpdir(), "stepfun-kb", "data");
    log(`[seekdb] 数据目录含非 ASCII 字符，改用 ${fallback}`);
    baseDir = fallback;
  }
  for (const sub of ["etc", "log", "run", "store"]) {
    fs.mkdirSync(path.join(baseDir, sub), { recursive: true });
  }

  const port = await findFreePort();
  log(`[seekdb] 启动 ${exe}（base-dir=${baseDir}, port=${port}）`);
  const child = spawn(exe, ["--base-dir", baseDir, "--port", String(port), "--nodaemon"], {
    cwd: nativeDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
  });
  let stderr = "";
  child.stdout.on("data", () => {});
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  let exited = false;
  child.once("exit", (code) => { exited = true; if (code !== 0) stderr += `\n(exit code ${code})`; });

  try {
    await waitForPort(port, timeoutMs, (elapsed) => {
      if (elapsed % 10000 < 500) log(`[seekdb] 仍在初始化… ${Math.round(elapsed / 1000)}s`);
    });
  } catch (error) {
    child.kill();
    throw new Error(
      `${error.message}\n--- seekdb stderr ---\n${stderr.trim()}\n--- seekdb.log 末尾 ---\n${tailLog(baseDir)}`,
    );
  }
  if (exited) {
    throw new Error(`seekdb 进程提前退出\n${stderr.trim()}\n${tailLog(baseDir)}`);
  }
  log(`[seekdb] 就绪 127.0.0.1:${port}`);

  return {
    host: "127.0.0.1",
    port,
    user: "root",
    baseDir,
    logTail: () => tailLog(baseDir),
    stop: () =>
      new Promise((resolve) => {
        if (child.killed) return resolve();
        child.once("exit", () => resolve());
        child.kill();
        setTimeout(() => resolve(), 5000);
      }),
  };
}
