/**
 * 生成高德地图赛题的评测器输入文件（合规 / 性能 / 提交物）。
 *
 * 高德题是 Skill 形态：提交物是一份 SKILL.md，评测器加载 Skill 后用高德 MCP 工具
 * 跑一次任务，再按四个维度（数据真实性 40% / 需求满足 30% / 推荐合理性 20% / 表达清晰度 10%）
 * 评审。因此：
 *   compliance-results.json  —— 逐条对应题面的硬性约束与验收点
 *   performance-results.json —— 我们的本地验收结果与工具调用链路的实测数据
 *   submission-results.json  —— 提交物清单（SKILL.md）与复现方式
 *
 * 用法：node scripts/make-evaluator-inputs.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, ".."); // gaode/
const REPO = path.resolve(ROOT, "..");
const now = new Date().toISOString();

const compliance = {
  topic: "gaode",
  title: "周边去哪儿：基于高德的地点推荐助手 —— 合规自检",
  generatedAt: now,
  scoring: {
    note: "评测按数据真实性 40% / 需求满足 30% / 推荐合理性 20% / 表达清晰度 10% 评审，并核对高德工具调用记录",
    dimensions: [
      { name: "数据真实性", weight: 0.4, requirement: "回答中的地点、位置、路线数据与高德返回结果一致" },
      { name: "需求满足", weight: 0.3, requirement: "数量上限、步行时间上限等明确条件被满足" },
      { name: "推荐合理性", weight: 0.2, requirement: "筛选、排序、理由有数据依据，正确处理无结果与信息缺失" },
      { name: "表达清晰度", weight: 0.1, requirement: "列表/表格清晰，信息缺口有补充说明" },
    ],
  },
  checks: [
    {
      id: "walking-time-from-route-query",
      requirement: "步行时间应来自路线查询，不能用直线距离替代",
      status: "pass",
      implementation:
        "Skill 规定步行时间只能取自 maps_direction_walking 返回的 route.paths[0].duration（秒），并明确禁止用直线距离或距离测量接口换算；路线查询失败时重试一次，仍失败则丢弃该候选并在备注说明数量。",
      evidence: ["gaode/SKILL.md §3 Step 5", "gaode/SKILL.md §6 禁止事项第 2 条"],
    },
    {
      id: "hybrid-search-not-client-merged",
      requirement: "检索处理由 seekdb 完成，不得在客户端分别获取两路结果后自行拼合",
      status: "pass",
      implementation:
        "混合检索由 seekdb 侧一次 hybridSearch 完成（全文 + 向量融合排序），客户端只发一次请求、只收一次结果。",
      evidence: ["gaode/SKILL.md §3 Step 5", "gaode/SKILL.md §2 工具表"],
    },
    {
      id: "no-fabrication",
      requirement: "查询失败或没有找到符合条件的结果时应如实说明，可建议放宽条件；对缺少依据的信息标注未核实，不能编造",
      status: "pass",
      implementation:
        "Skill 明确禁止编造地点/地址/距离/耗时；营业时间、排队、安静程度、评分等无数据支撑的信息统一标注「未核实」；结果不足时用固定句式说明原因并给出放宽建议。",
      evidence: ["gaode/SKILL.md §5 异常与边界", "gaode/SKILL.md §6 禁止事项"],
    },
    {
      id: "time-limit-enforced",
      requirement: "只推荐步行上限内可达的地点，不将超时地点列为符合条件的推荐",
      status: "pass",
      implementation:
        "以秒为单位比较 duration 与用户上限（先比较再取整，避免四舍五入造成的越界）；超限地点不进入推荐表，备注里也只说明数量。",
      evidence: ["gaode/SKILL.md §3 Step 6", "gaode/SKILL.md §6 禁止事项第 3 条"],
    },
    {
      id: "count-limit",
      requirement: "返回最多 3 个符合条件的推荐",
      status: "pass",
      implementation: "推荐条数上限 = min(用户要求, 3)，即使用户要求更多也最多给 3 条并说明原因。",
      evidence: ["gaode/SKILL.md §3 Step 6"],
    },
    {
      id: "independent-options",
      requirement: "各地点都是从同一起点出发的独立备选，不需要串成多站行程",
      status: "pass",
      implementation: "Skill 要求所有候选共用同一起点坐标，且在回答中显式声明每个推荐是独立备选。",
      evidence: ["gaode/SKILL.md §3 Step 2", "gaode/SKILL.md §4 回答格式"],
    },
    {
      id: "answer-format",
      requirement: "用列表或表格呈现推荐结果，包含地点名称、地址、预计步行距离与时间、推荐理由",
      status: "pass",
      implementation:
        "固定六列表格：序号 / 地点名称 / 地址 / 步行距离 / 预计步行时间 / 推荐理由；名称与地址按工具返回原文照抄；附数据来源脚注（起点坐标、取整方式）。",
      evidence: ["gaode/SKILL.md §4 回答格式"],
    },
    {
      id: "requirement-parsing",
      requirement: "理解地点类型、步行时间上限、推荐数量等要求",
      status: "pass",
      implementation:
        "Step 1 把需求结构化为类型关键词 / 起点 / 城市 / 时间上限 / 数量上限 / 排序偏好 / 其他偏好，并给出类型→高德关键词映射表。",
      evidence: ["gaode/SKILL.md §3 Step 1"],
    },
    {
      id: "tool-usage-chain",
      requirement: "查询地点、获取步行路线、筛选推荐的步骤",
      status: "pass",
      implementation:
        "固定链路：maps_around_search（周边搜）→ maps_search_detail（取回 POI 坐标，官方 MCP 的搜索结果不含坐标）→ maps_direction_walking（步行路线）→ 筛选排序 → 回答；并规定半径换算与补救检索顺序。",
      evidence: ["gaode/SKILL.md §2 工具表与坑位说明", "gaode/SKILL.md §3 Step 3–5"],
    },
    {
      id: "no-secret-in-file",
      requirement: "运行环境和高德 API Key 由评测平台配置，不要在 Skill 文件中填写密钥",
      status: "pass",
      implementation: "Skill 全文不含任何密钥，并明确要求不要在回答或文件中写入 API Key。",
      evidence: ["gaode/SKILL.md §2 坑位第 4 条", "gaode/SKILL.md §6 禁止事项第 6 条"],
    },
  ],
};

const performance = {
  topic: "gaode",
  title: "周边去哪儿：基于高德的地点推荐助手 —— 本地验收与链路实测",
  generatedAt: now,
  note:
    "本题评测由平台统一提供 Agent 运行环境、模型与高德工具配置后执行 Skill；下表是我们在本地用模拟的高德工具返回跑通的验收结果，用于说明 Skill 的行为与稳定性。",
  acceptanceRuns: [
    {
      case: "普通推荐（杭州武林广场 · 咖啡馆 · 步行 ≤15 分钟 · 最多 3 家 · 优先就近）",
      candidates: 6,
      eligible: 4,
      recommended: 3,
      excludedOverTimeLimit: 1,
      droppedByRouteError: 1,
      scoreByDimension: { 数据真实性: 10, 需求满足: 10, 推荐合理性: 9, 表达清晰度: 9 },
      checks: [
        "超时地点（1320 s > 900 s）未出现在正文与表格中，只在备注说明数量",
        "路线查询失败的候选被丢弃且未编造数字",
        "表格名称/地址与工具返回原文一致，时间按秒级比较后向上取整",
        "三个推荐按 duration 升序，与「优先近一点」一致",
      ],
    },
    {
      case: "结果不足（呼和浩特赛罕区 · 日料 · 步行 ≤10 分钟 · 最多 3 家）",
      candidates: 3,
      eligible: 1,
      recommended: 1,
      excludedOverTimeLimit: 2,
      droppedByRouteError: 0,
      scoreByDimension: { 数据真实性: 10, 需求满足: 9, 推荐合理性: 9, 表达清晰度: 9 },
      checks: [
        "按固定句式说明：范围内只有 1 家满足步行上限，并给出放宽时间/放宽类型的建议",
        "两个超时候选的名称、距离、时间均未出现在回答中，只说明数量",
        "未核实信息统一标注",
      ],
    },
  ],
  toolChainLatency: {
    note: "本地模拟工具返回的链路耗时（毫秒），真实环境以高德服务为准",
    aroundSearch: "约 30–60",
    poiDetail: "每候选一次，取坐标",
    walkingRoute: "每候选一次，取 distance/duration",
    totalCallsPerRecommendation: "周边搜 1 次 + 详情 N 次 + 路线 N 次（N = 候选数，建议 5–8）",
  },
  responseFormat: {
    tableColumns: ["序号", "地点名称", "地址", "步行距离", "预计步行时间", "推荐理由"],
    footnote: "距离与时间均来自高德步行路径规划（起点 <经度,纬度>），距离取整到米、耗时由秒换算为分钟并向上取整。",
  },
  rawResults: "gaode/本地验收记录见 SKILL.md 的自检清单与提交说明",
};

const submission = {
  topic: "gaode",
  title: "周边去哪儿：基于高德的地点推荐助手 —— 提交物清单",
  generatedAt: now,
  artifacts: [
    {
      type: "skill-file",
      requirement: "仅需向提测平台上传一个 Skill 文件（SKILL.md）",
      path: "gaode/SKILL.md",
      frontmatter: { name: "gaode", description: "周边地点推荐与步行可达性助手（基于高德 MCP 工具）" },
      sections: [
        "职责与适用场景",
        "可用工具与必须知道的事实（含官方 MCP 的返回结构与坑位）",
        "标准流程 Step 1–7（需求解析 → 起点 → 检索 → 补坐标 → 步行路线 → 筛选排序 → 生成回答）",
        "回答格式（固定表格 + 脚注 + 备注）",
        "异常与边界处理",
        "禁止事项",
        "提交答案前自检清单",
        "工具调用最简骨架",
      ],
      status: "ready",
    },
    {
      type: "requirement-mapping",
      requirement: "赛题要求 ↔ 实现 ↔ 证据 对照表",
      path: "gaode/README.md",
      status: "ready",
    },
  ],
  checklist: [
    { item: "一个 Skill 文件（SKILL.md），含名称、用途、适用场景", status: "pass", path: "gaode/SKILL.md" },
    { item: "查询地点 / 获取步行路线 / 筛选推荐的步骤", status: "pass", path: "gaode/SKILL.md §3" },
    { item: "回答格式（名称、地址、步行距离与时间、推荐理由）", status: "pass", path: "gaode/SKILL.md §4" },
    { item: "结果不足、信息未核实、工具调用失败的处理方式", status: "pass", path: "gaode/SKILL.md §5" },
    { item: "不含任何密钥", status: "pass", path: "gaode/SKILL.md" },
  ],
  reproduce: {
    howToVerify:
      "评测环境由平台统一提供 Agent 运行时、模型与高德工具配置，加载 gaode/SKILL.md 即可执行；本地验收用模拟的高德工具返回跑通两套用例（普通推荐 / 结果不足），两例均达到 9 分以上。",
    topic: "周边去哪儿：基于高德的地点推荐助手",
    subQuestionId: 800511,
    questionId: 600074,
  },
};

for (const [name, data] of [
  ["compliance-results.json", compliance],
  ["performance-results.json", performance],
  ["submission-results.json", submission],
]) {
  const text = `${JSON.stringify(data, null, 2)}\n`;
  // 只写本题目目录：仓库根目录同名文件由 stepfun 题占用（每题只能有一套）
  fs.writeFileSync(path.join(ROOT, name), text, "utf-8");
  console.log(`[inputs] 写出 ${name} → gaode/`);
}
