"""frontmatter - 解析 distill topic .md 的 YAML frontmatter

distill 输出契约（见 docs/design.md §5.2 + distill/SKILL.md）：
    文件头由 `---` 包裹一段 YAML，至少包含：
        type / date / session_id / ide / workspace
        scope / project / domain / general_category
        tags / quality{has_conclusion,has_code,estimated_value}
        source_msg_range

为了避免引入额外 PyYAML 依赖（目前项目零三方依赖），
这里实现一个最小可用的行级 YAML 解析器，覆盖以下字段形态：
    key: value                      # 标量
    key: null                       # 显式 null
    key: [a, b, c]                  # flow-style 数组
    key: "value with: colon"        # 引号字符串
    key:                            # 嵌套对象（仅支持一层缩进）
      sub: value
    key: [start, end]               # 整数数组（source_msg_range）

更复杂的多行结构（list of dict、引用锚点等）暂不支持；
distill 输出本身遵循扁平结构，足够覆盖。

输入：topic .md 文件路径或字符串内容
输出：dict[str, Any]

失败模式：
    - 没有 frontmatter (`---` 块缺失) → ValueError
    - YAML 解析异常（不识别的形态） → 记入 _parse_warnings 字段（不抛错），
      字段缺失由调用方校验
"""

import re
from pathlib import Path
from typing import Any


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'"):
        return s[1:-1]
    return s


def _parse_scalar(raw: str) -> Any:
    """解析标量值：null / true / false / 整数 / 浮点 / 字符串"""
    s = raw.strip()
    if s == "" or s.lower() == "null" or s == "~":
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    # 整数
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    # 浮点
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return _strip_quotes(s)


def _parse_flow_list(raw: str) -> list:
    """解析 flow-style 数组：[a, b, c] 或 [1, 2]"""
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return [_parse_scalar(inner)]
    inner = inner[1:-1].strip()
    if not inner:
        return []
    items = []
    # 简单按逗号分割（不支持嵌套数组/对象，frontmatter 用不上）
    for piece in inner.split(","):
        items.append(_parse_scalar(piece))
    return items


def parse_frontmatter(text: str) -> dict[str, Any]:
    """从 markdown 文件正文中提取 frontmatter 并解析为 dict

    实现策略：逐行扫描，识别一层缩进的嵌套对象（quality:）。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("文件缺少 YAML frontmatter (--- ... ---)")

    body = match.group(1)
    out: dict[str, Any] = {}
    warnings: list[str] = []

    current_key: str | None = None
    current_obj: dict[str, Any] | None = None

    for raw_line in body.splitlines():
        # 完全空行或注释行
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        # 顶层字段（无缩进）
        if not raw_line.startswith((" ", "\t")):
            current_key = None
            current_obj = None
            if ":" not in raw_line:
                warnings.append(f"无法解析行：{raw_line!r}")
                continue
            key, _, rest = raw_line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                # 嵌套对象的开始
                current_key = key
                current_obj = {}
                out[key] = current_obj
            elif rest.startswith("["):
                out[key] = _parse_flow_list(rest)
            else:
                out[key] = _parse_scalar(rest)
            continue

        # 缩进行（属于上一个 current_obj）
        if current_obj is None:
            warnings.append(f"孤立的缩进行（无所属父键）：{raw_line!r}")
            continue
        line = raw_line.strip()
        if ":" not in line:
            warnings.append(f"嵌套行无 colon：{raw_line!r}")
            continue
        sub_key, _, sub_rest = line.partition(":")
        sub_key = sub_key.strip()
        sub_rest = sub_rest.strip()
        if sub_rest.startswith("["):
            current_obj[sub_key] = _parse_flow_list(sub_rest)
        else:
            current_obj[sub_key] = _parse_scalar(sub_rest)

    if warnings:
        out["_parse_warnings"] = warnings
    return out


def parse_topic_file(path: Path) -> dict[str, Any]:
    """读取 topic .md 文件并返回 frontmatter dict"""
    if not path.exists():
        raise FileNotFoundError(f"topic 文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)
