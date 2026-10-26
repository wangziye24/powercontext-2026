# CourseNote — 学习资料混合检索助手

> 本项目由 **Qoder** 开发，使用 **seekdb**（向量 + 全文 + 结构化条件混合检索）实现核心能力。

- 项目设计：`docs/00-项目设计.md`
- Qoder 开发提示词（分步执行）：`docs/01-Qoder提示词.md`
- seekdb 建表与混合检索 SQL（已实测）：`docs/02-seekdb-schema.sql`
- 验证样例与预期输出：`docs/03-验证样例.md`

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 MINIMAX_API_KEY
python scripts/setup_db.py  # 建表 + 索引
python scripts/seed_data.py # 灌入示例资料
uvicorn app.main:app --host 0.0.0.0 --port 8080
python scripts/smoke_test.py
```

## 状态

实现由 Qoder 按 `docs/01-Qoder提示词.md` 的步骤 0~7 依次完成，本文件在实现过程中持续更新。
