"""
LawRefBook 法律数据库访问模块
数据来源：https://github.com/LawRefBook/Laws
"""

import re
import sqlite3
import time
import urllib.request
import urllib.parse
from pathlib import Path

DB_URL = "https://github.com/LawRefBook/Laws/raw/master/db.sqlite3"
DB_PATH = Path(__file__).parent / "law_db.sqlite3"
RAW_BASE = "https://raw.githubusercontent.com/LawRefBook/Laws/master"
MAX_CONTENT_CHARS = 8000


def ensure_db():
    """下载并缓存 SQLite 数据库（超过7天自动更新）"""
    if DB_PATH.exists():
        age_days = (time.time() - DB_PATH.stat().st_mtime) / 86400
        if age_days <= 7:
            return str(DB_PATH)
    print("正在从 GitHub 下载法律数据库...")
    urllib.request.urlretrieve(DB_URL, DB_PATH)
    print(f"数据库已保存至 {DB_PATH}")
    return str(DB_PATH)


def _get_conn():
    conn = sqlite3.connect(ensure_db())
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_law_raw(law_name: str) -> tuple[str, str]:
    """
    内部函数：查数据库 + 拉 GitHub 原始内容。
    返回 (full_text, display_name)，出错时 display_name 为空字符串。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT l.name, l.filename, c.folder
            FROM law l
            LEFT JOIN category c ON l.category_id = c.id
            WHERE l.name LIKE ? AND l.expired = 0
            ORDER BY l.publish DESC
            LIMIT 1
            """,
            (f"%{law_name}%",),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return f"未找到名称包含「{law_name}」的法律。", ""

    filename, folder, name = row["filename"], row["folder"], row["name"]
    if not filename or not folder:
        return f"找到法律「{name}」，但缺少文件路径信息。", ""

    url = f"{RAW_BASE}/{urllib.parse.quote(folder)}/{urllib.parse.quote(filename)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            content = resp.read().decode("utf-8")
        return f"# {name}\n\n{content}", name
    except Exception as e:
        return f"获取法律全文失败（{url}）：{e}", ""


def search_laws(keyword: str, limit: int = 10) -> list[dict]:
    """按关键词搜索法律名称，返回元信息列表（不含全文）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.name, l.subtitle, l.level, l.publish, l.valid_from,
                   l.expired, l.filename, c.name AS category, c.folder
            FROM law l
            LEFT JOIN category c ON l.category_id = c.id
            WHERE l.name LIKE ? AND l.expired = 0
            ORDER BY l.publish DESC
            LIMIT ?
            """,
            (f"%{keyword}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_categories() -> list[dict]:
    """列出所有法律分类。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            'SELECT id, name, folder, "group" FROM category ORDER BY "order"'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_law_content(law_name: str) -> str:
    """
    获取法律内容，截断至 MAX_CONTENT_CHARS 字符。
    如内容过长会提示使用 get_law_article 获取特定条款。
    """
    content, name = _fetch_law_raw(law_name)
    if not name:
        return content  # 错误信息直接返回

    if len(content) <= MAX_CONTENT_CHARS:
        return content

    total = len(content)
    truncated = content[:MAX_CONTENT_CHARS]
    last_nl = truncated.rfind("\n")
    if last_nl > 0:
        truncated = truncated[:last_nl]
    return (
        truncated
        + f"\n\n[内容已截断，原文约 {total} 字。"
        f"如需查询特定条款，请使用 get_law_article 工具并指定条款编号。]"
    )


def get_law_article(law_name: str, article_num: str) -> str:
    """
    从法律全文中提取特定条款。
    article_num 可为阿拉伯数字（如 "12"）或中文数字（如 "十二"）。
    """
    content, name = _fetch_law_raw(law_name)
    if not name:
        return content

    # 匹配 "第X条" 开头的段落，到下一个"第X条"或文末为止
    pattern = rf"(第{re.escape(article_num)}条[\s\S]*?)(?=第[零一二三四五六七八九十百千\d]+条|\Z)"
    match = re.search(pattern, content)
    if match:
        return f"《{name}》第{article_num}条：\n\n{match.group(1).strip()}"

    return f"在《{name}》中未找到第 {article_num} 条，请确认条款编号是否正确。"


# ── Claude tool_use 工具定义 ──────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_laws",
        "description": (
            "在中华人民共和国法律法规数据库中按关键词搜索法律名称。"
            "返回匹配的法律名称、分类、发布日期等元信息（不含全文）。"
            "适合用于：确认某部法律是否存在、获取相关法律列表。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词，如「劳动合同」「婚姻」「公司法」",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认10",
                    "default": 10,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_law_content",
        "description": (
            "获取指定法律的条文内容（最多返回8000字）。"
            "如法律较长会截断，此时应改用 get_law_article 查询具体条款。"
            "建议先用 search_laws 确认准确法律名称再调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "law_name": {
                    "type": "string",
                    "description": "法律名称，如「中华人民共和国劳动合同法」",
                }
            },
            "required": ["law_name"],
        },
    },
    {
        "name": "get_law_article",
        "description": (
            "从指定法律中精确提取某一条款的内容。"
            "适合用于：法律全文较长被截断时，精准获取特定条款。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "law_name": {
                    "type": "string",
                    "description": "法律名称，如「中华人民共和国劳动合同法」",
                },
                "article_num": {
                    "type": "string",
                    "description": "条款编号，支持阿拉伯数字（如 '47'）或中文数字（如 '四十七'）",
                },
            },
            "required": ["law_name", "article_num"],
        },
    },
]


def run_tool(name: str, inputs: dict) -> str:
    """执行工具调用，返回结果字符串。"""
    if name == "search_laws":
        results = search_laws(inputs["keyword"], inputs.get("limit", 10))
        if not results:
            return f"未找到包含「{inputs['keyword']}」的法律。"
        lines = [f"找到 {len(results)} 条结果：\n"]
        for r in results:
            pub = r.get("publish") or "未知"
            lines.append(f"- 【{r['category']}】{r['name']}（发布：{pub}）")
        return "\n".join(lines)

    if name == "get_law_content":
        return get_law_content(inputs["law_name"])

    if name == "get_law_article":
        return get_law_article(inputs["law_name"], inputs["article_num"])

    return f"未知工具：{name}"
