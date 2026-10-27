#!/usr/bin/env python3
"""生成《午夜便利店·阿夜的账本》的 Dify Chatflow DSL。

用法：
    python3 dify/scripts/build_dsl.py            # 输出到 dify/dsl/aye-ledger-chatflow.yml

为什么用脚本生成：
1. 13 个节点的 prompt / 代码 / HTTP body 都集中在一处，便于评审对照，也便于我们自己回归；
2. 生成结果就是提交的 DSL（导入 Dify 后再用 Dify 导出的版本覆盖，保证与 Dify 1.18 完全一致）。

节点链路（线性，保证执行顺序确定）：
    开始 → 意图判定(LLM) → 解析意图(Code) → 设定检索(HTTP→seekdb 侧车)
         → 记忆召回(HTTP→PowerContext) → 读取状态(HTTP→侧车) → 汇总上下文(Code)
         → 阿夜台词(LLM) → 状态机(Code) → 结局检索(HTTP→侧车) → 提取结局(Code)
         → 写入状态(HTTP→侧车) → 写入记忆(HTTP→PowerContext) → 回答(Answer)
"""
from __future__ import annotations

import json
import pathlib
import re
import uuid

import yaml

OUT = pathlib.Path(__file__).resolve().parents[1] / "dsl" / "aye-ledger-chatflow.yml"

# --------------------------------------------------------------------------- 可调参数
SIDECAR = "http://host.docker.internal:8090"      # 侧车服务（seekdb 检索 / 玩家状态）
POWERCONTEXT = "http://host.docker.internal:8848"  # PowerContext HTTP API
AGENT_ID = "aye-nightstore"
MODEL_PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"
MODEL_NAME = "MiniMax-M2.5"

# --------------------------------------------------------------------------- 节点 id
N = {
    "start": "1750000000001",
    "llm_intent": "1750000000002",
    "code_intent": "1750000000003",
    "http_lore": "1750000000004",
    "http_memory": "1750000000005",
    "http_state": "1750000000006",
    "code_ctx": "1750000000007",
    "llm_role": "1750000000008",
    "code_state": "1750000000009",
    "http_ending": "1750000000010",
    "code_end": "1750000000011",
    "http_state_write": "1750000000012",
    "http_memory_write": "1750000000013",
    "answer": "1750000000014",
}

POS = {
    "start": (0, 0),
    "llm_intent": (300, 0),
    "code_intent": (600, 0),
    "http_lore": (900, -160),
    "http_memory": (900, 0),
    "http_state": (900, 160),
    "code_ctx": (1200, 0),
    "llm_role": (1500, 0),
    "code_state": (1800, 0),
    "http_ending": (2100, 0),
    "code_end": (2400, 0),
    "http_state_write": (2700, -80),
    "http_memory_write": (2700, 80),
    "answer": (3000, 0),
}


def R(key: str, field: str) -> str:
    """Dify 变量引用必须用节点 id（不是标题/别名）。"""
    return "{{#" + N[key] + "." + field + "#}}"


ALIAS_REF_RE = re.compile(r"\{\{#([a-z_]+)\.([A-Za-z0-9_]+)#\}\}")


def resolve_aliases(obj):
    """把模板里的别名引用（如 {{#code_state.affection#}}）换成真实节点 id。

    Dify 的变量引用必须用节点 id（导出 DSL 里是一串数字），用别名会解析不到。
    """
    if isinstance(obj, dict):
        return {k: resolve_aliases(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_aliases(v) for v in obj]
    if isinstance(obj, str):
        def sub(m):
            key, field = m.group(1), m.group(2)
            if key not in N:
                return m.group(0)
            return "{{#" + N[key] + "." + field + "#}}"
        return ALIAS_REF_RE.sub(sub, obj)
    return obj


def node(key: str, data: dict, height: int = 100) -> dict:
    x, y = POS[key]
    return {
        "id": N[key],
        "type": "custom",
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "width": 244,
        "height": height,
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "data": data,
    }


def edge(src: str, dst: str, handle: str = "source") -> dict:
    return {
        "id": f"{N[src]}-{N[dst]}-{handle}",
        "source": N[src],
        "target": N[dst],
        "sourceHandle": handle,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
        "selected": False,
    }


def http_body(pairs: list[tuple[str, str]]) -> dict:
    """构造 body.type=json 的请求体。

    Dify 1.18 的规则：``type=json`` 时 ``data`` 只能有**一条**，其 value 是完整的 JSON 文本
    （老版本的 key/value 列表形式会报 "json body type should have exactly one item"）。
    """
    parts = []
    for k, v in pairs:
        if v.startswith("@@RAW@@"):
            # 原样插入的 JSON 片段（嵌套对象）
            parts.append(f"{json.dumps(k)}: {v[len('@@RAW@@'):]}")
        else:
            parts.append(f"{json.dumps(k)}: {json.dumps(v)}")
    text = "{" + ", ".join(parts) + "}"
    return {
        "type": "json",
        "data": [
            {"id": str(uuid.uuid4()), "type": "text", "value": text}
        ],
    }


def http_node(title: str, method: str, url: str, body=None, params: str = "") -> dict:
    data = {
        "title": title,
        "desc": "",
        "type": "http-request",
        "method": method,
        "url": url,
        "headers": "Content-Type: application/json",
        "params": params,
        "body": body or {"type": "none", "data": []},
        "authorization": {"type": "no-auth", "config": None},
        "timeout": {"max_connect_timeout": 10, "max_read_timeout": 60, "max_write_timeout": 60},
        "retry_config": {"max_retries": 2, "retry_enabled": True, "retry_interval": 1000},
        "selected": False,
        "variables": [],
    }
    return data


# --------------------------------------------------------------------------- 各节点
def build_start() -> dict:
    return node(
        "start",
        {
            "title": "开始",
            "desc": "玩家入口：player_id 决定记忆归属（同一个 ID = 同一段记忆）",
            "type": "start",
            "selected": False,
            "variables": [
                {
                    "label": "玩家ID（留空=默认 demo 玩家，换个 ID 就是换个人）",
                    "variable": "player_id",
                    "type": "text-input",
                    "required": False,
                    "max_length": 48,
                    "options": [],
                },
                {
                    "label": "玩家昵称（可选）",
                    "variable": "player_name",
                    "type": "text-input",
                    "required": False,
                    "max_length": 48,
                    "options": [],
                },
            ],
        },
        height=140,
    )


def build_llm_intent() -> dict:
    prompt = """你是「凌晨便利店」的剧情调度器。收银员阿夜毒舌、记仇、嘴上不饶人但手上照顾人。
请判断玩家这句话的意图，**只输出一个 JSON**，不要任何多余文字：
{"intent":"chat|order|prank|confront|ask|goodbye","target":"关键词","is_prank":false,"is_confront":false}

意图含义：
- chat：闲聊、打招呼、试探
- order：想买/问商品、价格、库存
- prank：想搞恶作剧、整蛊、捉弄阿夜
- confront：质疑阿夜记错了、追问"上次那件事"、或者在纠正阿夜的说法
- ask：询问设定、规则、玩法
- goodbye：道别

target 填玩家这次提到的东西（商品名、事件名、话题词）；没有就填空字符串。
is_prank / is_confront 表示这句话是否**明确**带着整蛊意图 / 质疑记错的意图。

玩家输入：{{#sys.query#}}"""
    return node(
        "llm_intent",
        {
            "title": "意图判定",
            "desc": "把玩家这句话归到 6 种意图之一",
            "type": "llm",
            "selected": False,
            "model": {
                "provider": MODEL_PROVIDER,
                "name": MODEL_NAME,
                "mode": "chat",
                "completion_params": {"temperature": 0.1, "max_tokens": 512},
            },
            "prompt_template": [
                {"id": str(uuid.uuid4()), "role": "system", "text": prompt}
            ],
            "context": {"enabled": False, "variable_selector": []},
            "memory": {
                "window": {"enabled": False, "size": 10},
                "query_prompt_template": "",
                "role_prefix": {"assistant": "", "user": ""},
            },
            "vision": {"enabled": False, "configs": {"detail": "high"}},
            "variables": [],
        },
        height=120,
    )


CODE_INTENT = '''import json
import re


def _parse(text):
    """从 LLM 输出里抠出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return {}
    m = re.search(r"\\{[\\s\\S]*\\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def main(role_text: str, player_id: str, sys_user_id: str, query: str) -> dict:
    data = _parse(role_text)
    intent = str(data.get("intent", "chat")).strip().lower()
    if intent not in ("chat", "order", "prank", "confront", "ask", "goodbye"):
        intent = "chat"
    target = str(data.get("target", "")).strip()
    truthy = (True, "true", "True", "1", 1)
    is_prank = 1 if (data.get("is_prank") in truthy or intent == "prank") else 0
    is_confront = 1 if (data.get("is_confront") in truthy or intent == "confront") else 0

    # 玩家标识：start.player_id > sys.user_id > demo
    pid = (player_id or "").strip() or (sys_user_id or "").strip() or "player_demo"

    # 根据意图决定去 seekdb 检索哪一类设定
    if intent in ("order", "ask"):
        kinds = "profile,item"
    elif intent == "prank":
        kinds = "prank,profile,item"
    elif intent == "confront":
        kinds = "rumor,profile,prank"
    else:
        kinds = "profile,item,rumor"

    topic = target or (query or "")[:30]
    memory_query = "玩家" + pid + " 关于" + topic + " 的偏好 口味 恶作剧 口头禅 历史互动"

    return {
        "intent": intent,
        "target": target,
        "lore_kinds": kinds,
        "is_prank": is_prank,
        "is_confront": is_confront,
        "player_id": pid,
        "memory_query": memory_query,
    }
'''


def build_code_intent() -> dict:
    return node(
        "code_intent",
        {
            "title": "解析意图",
            "desc": "把 LLM 的 JSON 变成后面节点能用的变量",
            "type": "code",
            "code_language": "python3",
            "code": CODE_INTENT,
            "selected": False,
            "variables": [
                {"variable": "role_text", "value_selector": [N["llm_intent"], "text"]},
                {"variable": "player_id", "value_selector": [N["start"], "player_id"]},
                {"variable": "sys_user_id", "value_selector": ["sys", "user_id"]},
                {"variable": "query", "value_selector": ["sys", "query"]},
            ],
            "outputs": {
                "intent": {"type": "string", "children": None},
                "target": {"type": "string", "children": None},
                "lore_kinds": {"type": "string", "children": None},
                "is_prank": {"type": "number", "children": None},
                "is_confront": {"type": "number", "children": None},
                "player_id": {"type": "string", "children": None},
                "memory_query": {"type": "string", "children": None},
            },
        },
        height=120,
    )


def build_http_lore() -> dict:
    return node(
        "http_lore",
        http_node(
            "设定检索（seekdb 混合检索）",
            "post",
            f"{SIDECAR}/lore/search",
            body=http_body(
                [
                    ("query", "{{#sys.query#}}"),
                    ("kinds", "{{#code_intent.lore_kinds#}}"),
                    ("top_k", "5"),
                ]
            ),
        )
        | {"desc": "向量 + 全文 + 结构化过滤，一次检索召回阿夜的设定/商品/事件/传闻"},
        height=120,
    )


def build_http_memory() -> dict:
    return node(
        "http_memory",
        http_node(
            "记忆召回（PowerContext）",
            "post",
            f"{POWERCONTEXT}/api/v1/memories/search",
            body=http_body(
                [
                    ("query", "{{#code_intent.memory_query#}}"),
                    ("user_id", "{{#code_intent.player_id#}}"),
                    ("agent_id", AGENT_ID),
                    ("limit", "8"),
                ]
            ),
        )
        | {"desc": "语义召回这个玩家之前留下的记忆（跨会话有效）"},
        height=120,
    )


def build_http_state() -> dict:
    return node(
        "http_state",
        http_node(
            "读取账本（seekdb）",
            "get",
            f"{SIDECAR}/state",
            params="player_id:{{#code_intent.player_id#}}",
        )
        | {"desc": "好感度 / 欠账 / 整蛊数 / 章节"},
        height=120,
    )


CODE_CTX = '''import json


def _loads(text):
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}


def main(lore_body: str, memory_body: str, state_body: str) -> dict:
    lore = _loads(lore_body)
    memory = _loads(memory_body)
    state = _loads(state_body)

    # 1) seekdb 检索结果（侧车已经渲染好 context）
    lore_ctx = lore.get("context") or ""
    if not lore_ctx:
        hits = lore.get("hits") or []
        lore_ctx = "\\n".join(
            "- [" + str(h.get("kind")) + "] " + str(h.get("title")) + "：" + str(h.get("content"))
            for h in hits
        )

    # 2) PowerContext 召回结果
    results = ((memory.get("data") or {}).get("results")) or []
    lines = []
    for r in results:
        content = str(r.get("content", ""))
        score = r.get("score")
        if isinstance(score, (int, float)):
            lines.append("- " + content + "（相似度 " + str(round(float(score), 3)) + "）")
        else:
            lines.append("- " + content)
    memory_ctx = "\\n".join(lines) if lines else "（关于这个玩家，你还什么都没记住）"

    # 3) 账本状态
    state_ctx = (
        "好感度=" + str(state.get("affection", 0))
        + "，欠账=" + str(state.get("balance", 0)) + " 元"
        + "，已完成整蛊=" + str(state.get("pranks_done", 0)) + " 件"
        + "，章节=" + str(state.get("chapter", "第一章·夜来客"))
        + "，黑名单=" + ("是" if state.get("blacklist") else "否")
    )

    return {"lore_ctx": lore_ctx, "memory_ctx": memory_ctx, "state_ctx": state_ctx}
'''


def build_code_ctx() -> dict:
    return node(
        "code_ctx",
        {
            "title": "汇总上下文",
            "desc": "把三个 HTTP 节点的 JSON 压成三段可直接进 prompt 的文本",
            "type": "code",
            "code_language": "python3",
            "code": CODE_CTX,
            "selected": False,
            "variables": [
                {"variable": "lore_body", "value_selector": [N["http_lore"], "body"]},
                {"variable": "memory_body", "value_selector": [N["http_memory"], "body"]},
                {"variable": "state_body", "value_selector": [N["http_state"], "body"]},
            ],
            "outputs": {
                "lore_ctx": {"type": "string", "children": None},
                "memory_ctx": {"type": "string", "children": None},
                "state_ctx": {"type": "string", "children": None},
            },
        },
        height=120,
    )


def build_llm_role() -> dict:
    prompt = """你是「凌晨便利店」的夜班收银员**阿夜**。凌晨 1:47 接班，5:00 交班。
说话简短、毒舌、记仇，但手上永远照顾人。只说人话，不写旁白。

玩家刚说了：{{#sys.query#}}
本轮意图：{{#code_intent.intent#}}（关键词：{{#code_intent.target#}}）

【店里查到的素材｜来自 seekdb 混合检索】
{{#code_ctx.lore_ctx#}}

【你记得关于这个玩家的东西｜来自 PowerContext 记忆】
{{#code_ctx.memory_ctx#}}
（说明：以 [偏好] / [恶作剧] / [梗] 开头的才是**关于这个玩家本人的事实**，回答时要优先用它们；
 [互动] 只是他随口说过的一句，别当成事实转述。）

【账本状态｜来自 seekdb】
{{#code_ctx.state_ctx#}}

请**只输出一个 JSON**（不要任何多余文字、不要代码块围栏）：
{"reply":"阿夜说的话（1-3 句，口语，可带一点毒舌）","affection_delta":整数,"balance_delta":数字,"memory_item":"一行记忆","hit_event":"用到的素材标题原文","action":"玩家这一轮实际做了什么","prank_done":"1 或 0"}

规则：
1. 好感度增减：普通闲聊 +2~+4；被整蛊 -3~-6（但如果你觉得好笑，可以 +2~+4）；玩家记住你的偏好、道歉、还钱、请你吃关东煮 +6~+10；冒犯或威胁 -5~-10。
2. memory_item 必须是**一行**，以方括号类别开头：[偏好] / [恶作剧] / [梗] / [状态] / [互动]，写这一轮值得记住的事（例："[偏好] 玩家小李每周三买热咖啡"）。没有值得记的就写 "[互动] 玩家说：<不超过30字>"。
3. 如果玩家在质疑你记错（意图是 confront）：优先用【你记得】里的真实记忆回答；如果【你记得】是空的，就把【店里查到的素材】里的一条**传闻**当成本玩家的"光荣事迹"说出来（这样玩家可以纠正你；下一轮你要认错，并把真话写进 memory_item）。
4. 绝对不要编造素材里没有的商品和价格；没有就直说没有。
5. hit_event 只能填素材里出现过的标题原文；没用到就填空字符串。
6. prank_done 在玩家这一轮真的完成/尝试了整蛊时填 "1"，否则填 "0"。
6.1 reply **必须**有内容（至少一句完整的话），绝对不能是空字符串；哪怕信息不足，也要用阿夜的口吻接一句。
7. 如果玩家提到你记得的旧事（【你记得】里有相关内容），reply 里**必须**主动提起那件旧事——这是本作的核心记忆玩法。"""
    return node(
        "llm_role",
        {
            "title": "阿夜台词",
            "desc": "结合设定 + 记忆 + 账本，生成阿夜的回应与状态增量",
            "type": "llm",
            "selected": False,
            "model": {
                "provider": MODEL_PROVIDER,
                "name": MODEL_NAME,
                "mode": "chat",
                "completion_params": {"temperature": 0.8, "max_tokens": 1024},
            },
            "prompt_template": [
                {"id": str(uuid.uuid4()), "role": "system", "text": prompt}
            ],
            "context": {"enabled": False, "variable_selector": []},
            "memory": {
                "window": {"enabled": False, "size": 10},
                "query_prompt_template": "",
                "role_prefix": {"assistant": "", "user": ""},
            },
            "vision": {"enabled": False, "configs": {"detail": "high"}},
            "variables": [],
        },
        height=140,
    )


CODE_STATE = '''import json
import re


def _loads(text):
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _bar(affection: int) -> str:
    filled = int(round((affection + 100) / 200 * 10))
    filled = max(0, min(10, filled))
    return "█" * filled + "░" * (10 - filled)


def main(role_text: str, state_body: str, query: str) -> dict:
    m = re.search(r"\\{[\\s\\S]*\\}", role_text or "")
    role = _loads(m.group(0)) if m else {}
    state = _loads(state_body)

    affection = int(_num(state.get("affection", 0)))
    balance = round(_num(state.get("balance", 0)), 2)
    blacklist = int(_num(state.get("blacklist", 0)))
    pranks = int(_num(state.get("pranks_done", 0)))
    old_chapter = str(state.get("chapter", "第一章·夜来客"))

    d_aff = int(_num(role.get("affection_delta", 0)))
    d_bal = round(_num(role.get("balance_delta", 0)), 2)
    if str(role.get("prank_done", "0")).strip().lower() in ("1", "true"):
        pranks += 1

    affection = max(-100, min(100, affection + d_aff))
    balance = round(balance + d_bal, 2)
    if affection <= -50:
        blacklist = 1

    if affection >= 85 and pranks >= 4:
        chapter = "第四章·合伙人"
    elif affection >= 60:
        chapter = "第三章·熟客"
    elif affection >= 30:
        chapter = "第二章·常客"
    else:
        chapter = "第一章·夜来客"

    # 只有"章节推进"或"被拉黑"时才播报结局/里程碑
    milestone = chapter != old_chapter and chapter in (
        "第二章·常客", "第三章·熟客", "第四章·合伙人"
    )
    blacklisted = affection <= -50
    ending_ready = 1 if (milestone or blacklisted) else 0
    ending_aff = affection if ending_ready else 9999

    reply = str(role.get("reply", "")).strip()
    if not reply:
        reply = "（阿夜翻了翻便签本，发现自己对这位客人……一无所知。）"

    memory_item = str(role.get("memory_item", "")).strip()
    if not memory_item:
        memory_item = "[互动] 玩家说：" + (query or "")[:30]

    return {
        "affection": affection,
        "balance": balance,
        "blacklist": blacklist,
        "pranks_done": pranks,
        "chapter": chapter,
        "reply": reply,
        "memory_item": memory_item,
        "hit_event": str(role.get("hit_event", "")).strip(),
        "action": str(role.get("action", "")).strip(),
        "affection_delta": d_aff,
        "affection_bar": _bar(affection),
        "ending_aff": ending_aff,
    }
'''


def build_code_state() -> dict:
    return node(
        "code_state",
        {
            "title": "状态机",
            "desc": "好感度结算、欠账、整蛊计数、章节推进、结局判定",
            "type": "code",
            "code_language": "python3",
            "code": CODE_STATE,
            "selected": False,
            "variables": [
                {"variable": "role_text", "value_selector": [N["llm_role"], "text"]},
                {"variable": "state_body", "value_selector": [N["http_state"], "body"]},
                {"variable": "query", "value_selector": ["sys", "query"]},
            ],
            "outputs": {
                "affection": {"type": "number", "children": None},
                "balance": {"type": "number", "children": None},
                "blacklist": {"type": "number", "children": None},
                "pranks_done": {"type": "number", "children": None},
                "chapter": {"type": "string", "children": None},
                "reply": {"type": "string", "children": None},
                "memory_item": {"type": "string", "children": None},
                "hit_event": {"type": "string", "children": None},
                "action": {"type": "string", "children": None},
                "affection_delta": {"type": "number", "children": None},
                "affection_bar": {"type": "string", "children": None},
                "ending_aff": {"type": "number", "children": None},
            },
        },
        height=140,
    )


def build_http_ending() -> dict:
    return node(
        "http_ending",
        http_node(
            "结局检索（seekdb）",
            "post",
            f"{SIDECAR}/lore/search",
            body=http_body(
                [
                    ("query", "结局"),
                    ("kinds", "ending"),
                    ("min_affection", "{{#code_state.ending_aff#}}"),
                    ("max_affection", "{{#code_state.ending_aff#}}"),
                    ("min_pranks", "{{#code_state.pranks_done#}}"),
                    ("top_k", "1"),
                ]
            ),
        )
        | {"desc": "按好感度区间 + 整蛊数做结构化过滤，命中才播报结局"},
        height=120,
    )


CODE_END = '''import json


def main(ending_body: str) -> dict:
    try:
        data = json.loads(ending_body or "{}")
    except Exception:
        data = {}
    hits = data.get("hits") or []
    text = ""
    if hits:
        h = hits[0]
        text = "\\n🏁 **" + str(h.get("title")) + "**　" + str(h.get("content"))
    return {"ending_text": text}
'''


def build_code_end() -> dict:
    return node(
        "code_end",
        {
            "title": "提取结局",
            "desc": "把命中/未命中转成一句可直接展示的话",
            "type": "code",
            "code_language": "python3",
            "code": CODE_END,
            "selected": False,
            "variables": [
                {"variable": "ending_body", "value_selector": [N["http_ending"], "body"]}
            ],
            "outputs": {"ending_text": {"type": "string", "children": None}},
        },
        height=100,
    )


def build_http_state_write() -> dict:
    return node(
        "http_state_write",
        http_node(
            "写入账本（seekdb）",
            "post",
            f"{SIDECAR}/state",
            body=http_body(
                [
                    ("player_id", "{{#code_intent.player_id#}}"),
                    ("player_name", "{{#start.player_name#}}"),
                    ("affection", "{{#code_state.affection#}}"),
                    ("balance", "{{#code_state.balance#}}"),
                    ("blacklist", "{{#code_state.blacklist#}}"),
                    ("pranks_done", "{{#code_state.pranks_done#}}"),
                    ("chapter", "{{#code_state.chapter#}}"),
                    (
                        "event",
                        '@@RAW@@{"turn_id":"{{#sys.conversation_id#}}","intent":"{{#code_intent.intent#}}",'
                        '"action":"{{#code_state.action#}}","affection_delta":{{#code_state.affection_delta#}},'
                        '"balance_delta":0,"hit_event":"{{#code_state.hit_event#}}"}',
                    ),
                ]
            ),
        )
        | {"desc": "状态 + 事件流水一起落库（event_log 可核对）"},
        height=120,
    )


def build_http_memory_write() -> dict:
    return node(
        "http_memory_write",
        http_node(
            "写入记忆（PowerContext）",
            "post",
            f"{POWERCONTEXT}/api/v1/memories",
            body=http_body(
                [
                    ("content", "{{#code_state.memory_item#}}"),
                    ("user_id", "{{#code_intent.player_id#}}"),
                    ("agent_id", AGENT_ID),
                    ("scope", "user"),
                    ("memory_type", "working"),
                    ("infer", "false"),
                ]
            ),
        )
        | {"desc": "本轮值得记住的事写进 PowerContext，下次会话会被召回"},
        height=120,
    )


def build_answer() -> dict:
    answer = """{{#code_state.reply#}}

---

**🧾 阿夜的账本**

- 好感度：{{#code_state.affection#}} / 100　{{#code_state.affection_bar#}}
- 欠账：{{#code_state.balance#}} 元
- 已完成整蛊：{{#code_state.pranks_done#}} 件
- 章节：{{#code_state.chapter#}}
{{#code_end.ending_text#}}"""
    return node(
        "answer",
        {
            "title": "回答",
            "desc": "阿夜的台词 + 今日账本",
            "type": "answer",
            "selected": False,
            "answer": answer,
            "variables": [],
        },
        height=160,
    )


def build_graph() -> dict:
    nodes = [
        build_start(),
        build_llm_intent(),
        build_code_intent(),
        build_http_lore(),
        build_http_memory(),
        build_http_state(),
        build_code_ctx(),
        build_llm_role(),
        build_code_state(),
        build_http_ending(),
        build_code_end(),
        build_http_state_write(),
        build_http_memory_write(),
        build_answer(),
    ]
    order = [
        ("start", "llm_intent"),
        ("llm_intent", "code_intent"),
        ("code_intent", "http_lore"),
        ("http_lore", "http_memory"),
        ("http_memory", "http_state"),
        ("http_state", "code_ctx"),
        ("code_ctx", "llm_role"),
        ("llm_role", "code_state"),
        ("code_state", "http_ending"),
        ("http_ending", "code_end"),
        ("code_end", "http_state_write"),
        ("http_state_write", "http_memory_write"),
        ("http_memory_write", "answer"),
    ]
    edges = [edge(a, b) for a, b in order]
    return {
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0, "y": 0, "zoom": 0.6},
    }


def build_dsl() -> dict:
    return {
        "app": {
            "description": "凌晨 1:47 的街角便利店。收银员阿夜记得你所有的小动作——你换掉的那包薄荷糖、"
                           "你说过的胡话、你欠的账。记忆存在 PowerContext，设定与账本存在 seekdb，"
                           "整条流程由 Dify 编排。",
            "icon": "🏪",
            "icon_background": "#FFEAD5",
            "mode": "advanced-chat",
            "name": "午夜便利店·阿夜的账本",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": "app",
        "version": "0.3.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {
                    "allowed_file_extensions": [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
                    "allowed_file_types": ["image"],
                    "allowed_file_upload_methods": ["local_file", "remote_url"],
                    "enabled": False,
                    "fileUploadConfig": {
                        "audio_file_size_limit": 50,
                        "batch_count_limit": 5,
                        "file_size_limit": 15,
                        "image_file_size_limit": 10,
                        "video_file_size_limit": 100,
                        "workflow_file_upload_limit": 10,
                    },
                    "image": {
                        "enabled": False,
                        "number_limits": 3,
                        "transfer_methods": ["local_file", "remote_url"],
                    },
                    "number_limits": 3,
                },
                "opening_statement": "凌晨 1:47，便利店的灯刚亮。收银台后面的人抬头看了你一眼——"
                                     "「又是你。要买什么，还是又来搞事？」",
                "retriever_resource": {"enabled": False},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                    "关东煮还有吗？",
                    "我叫小李，以后每周三都来买热咖啡",
                    "把薄荷糖的标签换成『后悔药』怎么样？",
                    "你还记得我上次干了什么吗？",
                    "我还欠多少钱？",
                ],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": build_graph(),
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT), help="输出路径")
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = resolve_aliases(build_dsl())
    out.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100000),
        encoding="utf-8",
    )
    nodes = data["workflow"]["graph"]["nodes"]
    print(f"written: {out}")
    print(f"nodes={len(nodes)} edges={len(data['workflow']['graph']['edges'])}")


if __name__ == "__main__":
    main()
