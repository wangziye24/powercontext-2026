/**
 * 生成 MiniMax 题（「规有所依」SDD 上下文管理系统）的评测器输入文件。
 *
 * 该题交付物就是 minimax/ 目录本身：sddctx 工程上下文模块
 * （seekdb 存工程资料并检索 → MiniMax 真实改码 → PowerContext 存任务状态）。
 * 评测器要求三份结果文件：compliance / performance / submission。
 *
 * 用法：node scripts/make-evaluator-inputs.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, ".."); // minimax/
const now = new Date().toISOString();

const compliance = {
  topic: "minimax",
  title: "规有所依，面向软件持续演进的 SDD 上下文管理系统 —— 合规自检",
  generatedAt: now,
  summary: {
    note: "逐条对应赛题 4.1 的 6 项必做验收；一键复现命令为 bash scripts/reproduce.sh",
    verifier: "scripts/reproduce.sh（逐项打印 PASS/FAIL，退出码 0 表示全部通过）",
  },
  checks: [
    {
      id: "runnable",
      requirement: "能够运行：按运行说明可以初始化数据、启动模块并执行验证样例，不依赖未交付的本地文件或状态",
      status: "pass",
      implementation:
        "setup_wsl.sh 建虚拟环境并安装依赖（含 pytest），seekdb 自检；reproduce.sh 用全新的 $SDDCTX_HOME 与临时工作工程，从零建库、建 Scope 跑完整流程。资料、测试、修改前快照（snapshots/before）全部随仓库交付。",
      evidence: ["scripts/setup_wsl.sh", "scripts/reproduce.sh", "artifacts/logs/"],
    },
    {
      id: "minimax-participation",
      requirement: "MiniMax 实际参与：开发记录包含本次任务、提供的上下文和代码修改过程，并与提交的代码差异对应",
      status: "pass",
      implementation:
        "sddctx apply 通过 OpenAI 兼容 HTTP API 调用 MiniMax-M3.1-Flash，把检索出的规格/代码/测试渲染成提示，模型返回 unified diff 后由模块解析并应用。原始请求、响应、token 用量全部留档。",
      evidence: [
        "artifacts/minimax/minimax-call-20261026T071003.json",
        "artifacts/minimax/minimax-prompt-20261026T071003.txt",
        "artifacts/diff/minimax-20261026T071003.patch",
        "docs/MINIMAX-USAGE.md",
      ],
    },
    {
      id: "context-correct",
      requirement: "上下文正确：输出包含样例规定的必要规格、代码和测试来源；规格更新并重新导入后使用更新内容",
      status: "pass",
      implementation:
        "按任务检索 7 条规格（SPEC-004 标为本次要实现，其余标为必须保持）、待改函数 filter_orders（含行号区间与关联规格）、8 个目标测试与 13 个回归测试，全部带 source_path 与 source_hash；规格改动后重新 ingest，changed_files 精确命中 SPEC-004，正文与哈希随之变化。",
      evidence: ["artifacts/context/context.json", "artifacts/spec-change/", "docs/VERIFICATION.md"],
    },
    {
      id: "database-participation",
      requirement: "数据库实际参与：seekdb／OceanBase 中保存了工程资料或索引，实际查询结果用于任务上下文",
      status: "pass",
      implementation:
        "内嵌 seekdb（pylibseekdb）保存文档全文、规格条目、代码符号与测试函数；上下文召回全部由 SQL 完成，SQL 记录随证据交付。导入统计：8 个文档 / 7 条规格 / 8 个代码符号 / 21 个测试函数。",
      evidence: ["artifacts/db/ingest-*.sql.txt", "artifacts/db/context-*.sql.txt", "src/sddctx/retrieve.py"],
    },
    {
      id: "state-reuse",
      requirement: "状态能够复用：从空状态开始保存任务记录，重启模块后能够读取，并用于后续上下文或任务处理",
      status: "pass",
      implementation:
        "任务阶段、已完成、待办、目标/全量测试退出码与日志路径写入 PowerContext（内嵌 seekdb）；重启 Server 后 sddctx state show 仍可读回，下一次 apply 会把上次状态拼进提示。任务 key 用任务文本的 SHA-1 前 12 位，跨进程稳定。",
      evidence: ["artifacts/state/verify-*.json", "src/sddctx/taskstate.py"],
    },
    {
      id: "code-change-effective",
      requirement:
        "代码修改有效：同一组目标功能测试修改前因目标行为未实现而失败，修改后全部通过；相关回归测试也全部通过",
      status: "pass",
      implementation:
        "修改前快照 snapshots/before 的 8 个目标用例全部因 TypeError: unexpected keyword argument 'tier' 失败（功能未实现，非环境错误），回归 13 个通过；应用模型补丁后目标 8 个通过、全量 21 个通过；补丁产物与 sample-project 逐字节一致（IDENTICAL）。",
      evidence: [
        "artifacts/logs/pytest-before-*.log",
        "artifacts/logs/pytest-after-*.log",
        "artifacts/diff/committed-before-to-after.diff",
        "snapshots/before/",
      ],
    },
  ],
};

const performance = {
  topic: "minimax",
  title: "规有所依，面向软件持续演进的 SDD 上下文管理系统 —— 实测数据",
  generatedAt: now,
  sampleCase: {
    task: "在订单筛选中增加客户等级过滤（customer_tier），同时保持 SPEC-001 ~ SPEC-003、SPEC-005 ~ SPEC-007 原有行为不变",
    specs: 7,
    codeSymbols: 8,
    targetTests: 8,
    regressionTests: 13,
    documents: 8,
  },
  beforeAfter: {
    beforeCodeChange: {
      targetTests: "8 failed（功能未实现：TypeError: filter_orders() got an unexpected keyword argument 'tier'）",
      regressionTests: "13 passed",
      exitCode: 1,
    },
    afterCodeChange: {
      targetTests: "8 passed",
      regressionTests: "13 passed",
      total: "21 passed",
      exitCode: 0,
    },
    patchEquivalence: "IDENTICAL（模型补丁应用到修改前快照后，与 sample-project 逐字节一致）",
  },
  minimaxCall: {
    model: "MiniMax-M3.1-Flash",
    endpoint: "https://api.minimaxi.com/v1/chat/completions",
    latencySeconds: 102.9,
    inputTokens: 4283,
    outputTokens: 9150,
    reasoningTokens: 8560,
    contentTokens: 590,
    finishReason: "stop",
    record: "artifacts/minimax/minimax-call-20261026T071003.json",
  },
  stateReuse: {
    note: "停止并重启 PowerContext Server 后，任务状态仍可读回（阶段、已完成、待办、目标测试与全量测试退出码、日志路径）",
  },
  reproduce: {
    environment: "Linux 或 WSL Ubuntu；Python 3.11~3.14（3.12 实测）；内嵌 seekdb 的数据目录必须放在 Linux 原生文件系统上（不能是 /mnt/c、/mnt/d）",
    needsApiKey: false,
    steps: [
      "bash scripts/setup_wsl.sh",
      "bash scripts/reproduce.sh",
    ],
    notes: [
      "没有 MINIMAX_API_KEY 时，apply 步骤自动使用 artifacts/diff/minimax-*.patch 回放已记录的模型产出",
      "所有数据目录与工作工程都在临时目录，仓库与已有状态不受影响",
    ],
  },
  rawResults: "artifacts/（SQL 记录、MiniMax 请求响应、测试日志、代码差异、状态快照、规格变更对比）",
};

const submission = {
  topic: "minimax",
  title: "规有所依，面向软件持续演进的 SDD 上下文管理系统 —— 提交物清单",
  generatedAt: now,
  artifacts: [
    {
      type: "complete-source-code",
      requirement: "完整源码：上下文模块、AI 助手接入代码及运行所需的配套程序",
      path: "minimax/src/sddctx",
      entry: "minimax/src/sddctx/cli.py（CLI 入口 sddctx）",
      components: [
        "ingest.py / schema.py / db.py / retrieve.py —— 工程资料入库 seekdb 并检索上下文",
        "minimax_client.py / patching.py —— 真实调用 MiniMax 产出并应用 unified diff（含 --replay 回放模式）",
        "taskstate.py —— 任务状态写入 PowerContext 并读回",
        "context.py —— 把检索结果渲染成评测样例要求的任务上下文与提示词",
      ],
      status: "ready",
    },
    {
      type: "run-instructions",
      requirement: "运行说明：依赖版本、配置步骤、数据初始化、调用入口、测试命令及已知限制",
      path: "minimax/docs/RUNBOOK.md",
      quickstart: ["bash scripts/setup_wsl.sh", "bash scripts/reproduce.sh"],
      status: "ready",
    },
    {
      type: "verification-sample",
      requirement: "验证样例：任务描述、有效规格、初始数据、操作步骤、预期结果及重置方法",
      path: "minimax/docs/VERIFICATION.md",
      beforeSnapshot: "minimax/snapshots/before",
      afterSnapshot: "minimax/sample-project",
      status: "ready",
    },
    {
      type: "code-and-tests",
      requirement: "代码与测试：修改前后代码快照、代码差异、新增功能测试、相关回归测试及运行日志",
      paths: [
        "minimax/snapshots/before",
        "minimax/sample-project",
        "minimax/artifacts/diff",
        "minimax/artifacts/logs",
        "minimax/artifacts/state",
      ],
      status: "ready",
    },
    {
      type: "technical-usage-record",
      requirement: "技术使用记录：MiniMax 开发记录、数据库查询记录、PowerContext 状态写入与复用记录",
      paths: ["minimax/artifacts/minimax", "minimax/artifacts/db", "minimax/docs/MINIMAX-USAGE.md"],
      status: "ready",
    },
    {
      type: "one-page-technical-note",
      requirement: "一页技术说明：模块结构、上下文选择方式和各项技术的分工",
      path: "minimax/docs/ARCHITECTURE.md",
      status: "ready",
    },
  ],
  checklist: [
    { item: "能够运行（无密钥亦可一键复现）", status: "pass", path: "scripts/reproduce.sh" },
    { item: "MiniMax 实际参与并与提交差异对应", status: "pass", path: "artifacts/minimax/" },
    { item: "上下文正确（规格/代码/测试来源真实）", status: "pass", path: "artifacts/context/context.json" },
    { item: "数据库实际参与（seekdb SQL 召回）", status: "pass", path: "artifacts/db/" },
    { item: "状态能够复用（重启后可读回）", status: "pass", path: "artifacts/state/" },
    { item: "代码修改有效（改前失败、改后全过）", status: "pass", path: "artifacts/logs/" },
  ],
  reproduce: {
    topic: "规有所依，面向软件持续演进的 SDD 上下文管理系统",
    subQuestionId: 800514,
    questionId: 600077,
    command: "bash scripts/reproduce.sh",
  },
};

for (const [name, data] of [
  ["compliance-results.json", compliance],
  ["performance-results.json", performance],
  ["submission-results.json", submission],
]) {
  const text = `${JSON.stringify(data, null, 2)}\n`;
  fs.writeFileSync(path.join(ROOT, name), text, "utf-8");
  console.log(`[inputs] 写出 ${name} → minimax/`);
}
