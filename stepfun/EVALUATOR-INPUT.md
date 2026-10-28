# 评测器输入说明

本目录（`stepfun/`）随交付物提供三份结果文件，作为平台评测器的输入：

| 文件 | 内容 | 对应赛题条款 |
| --- | --- | --- |
| `compliance-results.json` | 逐条合规自检：要求 / 我们的实现 / 证据位置 / 结论 | 2.5 通用要求、3.1 验收标准 |
| `performance-results.json` | 固定测试条件、各阶段耗时、模型体积与压缩比、优化前后对比、检索质量 | 2.4 性能与质量、3.2 性能指标 |
| `submission-results.json` | 提交物清单（可运行 Web 应用 / 完整源码 / 一页技术说明）与复现步骤 | 4.1–4.3 提交物要求 |

三份文件由 `scripts/make-evaluator-inputs.mjs` 生成，性能数字直接取自 `bench/results/bench.json`
（`bench/run-bench.mjs` 的实测输出），不手写、不与报告脱节：

```bash
node scripts/make-evaluator-inputs.mjs
```

JSON 结构为自解释结构，键名含义：

- `topic`：题目标识（`stepfun`）。
- `checks[]`：合规条目，字段为 `requirement` / `status` / `implementation` / `evidence`。
- `cases[]`：每个「精度 × 后端」配置一行，字段为各阶段毫秒数、吞吐、Recall@5、nDCG@5。
- `fixedConditions`：测试条件（设备、浏览器、模型版本、批大小、seekdb 索引参数、语料规模、计时口径）。
- `artifacts[]` / `checklist[]`：提交物清单与逐项自检。
