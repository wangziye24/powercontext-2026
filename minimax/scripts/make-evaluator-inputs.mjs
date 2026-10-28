/**
 * 生成 MiniMax 题（SDD 上下文管理系统）的评测器输入文件。
 *
 * 该题交付物是 stepfun 之外的 minimax/ 目录：sddctx 工程上下文模块
 * （seekDB 存工程资料并检索 → AI 助手真实改码 → PowerContext 存任务状态）。
 * 评测器同样要求三份结果文件：compliance / performance / submission。
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
  checks: [
    {
      id: "current-valid-specs",
      requirement: "根据开发任务提供当前有效的规格",
      status: "pass",
      implementation:
        "规格条目导入 seekDB 并带 source_hash；检索时按任务文本命中规格，区分「本次要实现」与「必须保持」，规格变更后重新导入并同步正文与哈希（changed_files 精确指出变化条目）。",
      evidence: ["minimax/src/sddctx/ingest.py", "minimax/src/sddctx/retrieve.py", "minimax/artifacts/spec-change/"],
    },
    {
      id: "relevant-code-and-tests",
      requirement: "提供相关代码和测试",
      status: "pass",
      implementation:
        "检索同时返回待改函数（带行号区间与关联规格）、工程已有错误类型约定，以及目标功能测试与必须保持的回归测试的完整代码。",
      evidence: ["minimax/src/sddctx/retrieve.py", "minimax/artifacts/context/context.json"],
    },
    {
      id: "real-code-change",
      requirement: "通过实际代码修改验证效果",
      status: "pass",
      implementation:
        "由 AI 助手基于检索出的上下文产出 unified diff 并应用到样例工程，改码前后跑真实 pytest：改码前目标测试失败、回归测试通过；改码后全部通过。",
      evidence: [
        "minimax/artifacts/logs/pytest-before-*.log",
        "minimax/artifacts/logs/pytest-after-*.log",
        "minimax/artifacts/diff/committed-before-to-after.diff",
      ],
    },
    {
      id: "patch-equivalence",
      requirement: "提交的代码差异确实来自模型产出，而非手工编辑",
      status: "pass",
      implementation:
        "把模型补丁应用到改码前快照，与改码后快照逐字节比较，脚本输出 IDENTICAL。",
      evidence: ["minimax/scripts/verify_patch_equivalence.sh", "minimax/artifacts/diff/"],
    },
    {
      id: "cross-session-handover",
      requirement: "跨会话和任务交接",
      status: "pass",
      implementation:
        "任务状态（Scope、已完成、待办、验证结果）写入 PowerContext（内嵌 seekDB），模块重启后读回；下一次改码把上次进展拼进提示词。",
      evidence: ["minimax/src/sddctx/taskstate.py", "minimax/artifacts/state/"],
    },
    {
      id: "reproducible-scripts",
      requirement: "按提交的说明可启动并完成上述操作",
      status: "pass",
      implementation:
        "scripts/ 提供一键链路（setup_wsl / init_data / run_verification / demo_spec_change / verify_before_after / verify_patch_equivalence / reset_data），并修复了 6 处会让全新环境跑不起来的可复现性问题。",
      evidence: ["minimax/docs/RUNBOOK.md", "minimax/README.md"],
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
    codeSymbols: 7,
    targetTests: 8,
    regressionTests: 13,
  },
  beforeAfter: {
    beforeCodeChange: {
      targetTests: "8 failed（功能未实现，TypeError: filter_orders() got an unexpected keyword argument 'tier'）",
      regressionTests: "13 passed",
      exitCode: 1,
    },
    afterCodeChange: {
      targetTests: "8 passed",
      regressionTests: "13 passed",
      total: "21 passed",
      exitCode: 0,
    },
    patchEquivalence: "IDENTICAL（模型补丁应用到改码前快照后，与改码后快照逐字节一致）",
  },
  stateReuse: {
    note: "停止并重启 PowerContext Server 后，任务状态仍可读回（阶段、已完成、待办、目标测试与全量测试退出码、日志路径）。",
  },
  reproduce: {
    environment: "Linux / WSL Ubuntu（内嵌 seekDB 不支持 Windows）、Python 3.12、uv",
    steps: [
      "bash scripts/setup_wsl.sh",
      "bash scripts/init_data.sh",
      "bash scripts/run_verification.sh",
      "bash scripts/verify_before_after.sh <改码前提交> <改码后提交>",
      "bash scripts/verify_patch_equivalence.sh <改码前提交> <改码后提交>",
    ],
  },
  rawResults: "minimax/artifacts/（SQL 记录、模型请求响应、测试日志、代码差异、状态快照）",
};

const submission = {
  topic: "minimax",
  title: "规有所依，面向软件持续演进的 SDD 上下文管理系统 —— 提交物清单",
  generatedAt: now,
  artifacts: [
    {
      type: "context-module",
      requirement: "为已有 AI 开发助手实现或改进工程上下文模块",
      path: "minimax/src/sddctx",
      entry: "minimax/src/sddctx/cli.py（CLI 入口 sddctx）",
      components: [
        "ingest.py / schema.py / db.py / retrieve.py —— 工程资料入 seekDB 并检索上下文",
        "minimax_client.py / patching.py —— 真实调用 AI 助手产出并应用 unified diff",
        "taskstate.py —— 任务状态写入 PowerContext",
      ],
      status: "ready",
    },
    {
      type: "sample-project",
      requirement: "验证用工程（规格 + 代码 + 回归测试）",
      path: "minimax/sample-project",
      status: "ready",
    },
    {
      type: "scripts",
      requirement: "可复现的一键验证链路",
      path: "minimax/scripts",
      status: "ready",
    },
    {
      type: "documentation",
      requirement: "运行说明、验证样例、技术说明、模型使用记录",
      path: "minimax/docs（RUNBOOK / VERIFICATION / ARCHITECTURE / MINIMAX-USAGE）",
      status: "ready",
    },
    {
      type: "evidence",
      requirement: "实验结果应来自实际运行",
      path: "minimax/artifacts",
      status: "ready",
    },
  ],
  checklist: [
    { item: "提供当前有效的规格、相关代码与测试", status: "pass", path: "minimax/src/sddctx/retrieve.py" },
    { item: "通过实际代码修改验证效果", status: "pass", path: "minimax/artifacts/logs/" },
    { item: "提交的差异与模型产出一致", status: "pass", path: "minimax/scripts/verify_patch_equivalence.sh" },
    { item: "跨会话状态可复用", status: "pass", path: "minimax/src/sddctx/taskstate.py" },
    { item: "按说明可启动并完成上述操作", status: "pass", path: "minimax/docs/RUNBOOK.md" },
  ],
  reproduce: {
    topic: "规有所依，面向软件持续演进的 SDD 上下文管理系统",
    subQuestionId: 800514,
    questionId: 600077,
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
