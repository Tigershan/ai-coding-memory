"""core.frontmatter - 零依赖 YAML frontmatter 解析

足够 memory_store 用：支持 string / int / float / bool / null / list / 简单嵌套 dict。
不支持复杂 YAML 特性（多行字符串折叠、anchor、tag 等）。
对超出能力的字段会保留为原始字符串，不抛异常。

两种 IO 模式：
    parse(text) -> (frontmatter_dict, body)
    dump(frontmatter_dict, body) -> text
"""

from __future__ import annotations

import re
from typing import Any


_FRONTMATTER_DELIM = "---"


def parse(text: str) -> tuple[dict, str]:
    """解析 markdown 文本，返回 (frontmatter, body)。
    无 frontmatter 时返回 ({}, 全文)。"""
    if not text.startswith(_FRONTMATTER_DELIM + "\n") and text != _FRONTMATTER_DELIM:
        return {}, text

    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    # 去掉 body 前导空行
    body = body.lstrip("\n")
    return _parse_yaml(fm_text), body


def dump(fm: dict, body: str) -> str:
    """把 frontmatter dict + body 序列化为 markdown 文本。"""
    fm_lines = _dump_yaml(fm)
    parts = [_FRONTMATTER_DELIM, *fm_lines, _FRONTMATTER_DELIM, "", body.lstrip("\n")]
    return "\n".join(parts)


# ==================== 内部 parser ====================

_KV_PATTERN = re.compile(r"^(\s*)([A-Za-z_][\w\-]*)\s*:\s*(.*)$")
_LIST_PATTERN = re.compile(r"^(\s*)-\s*(.*)$")


def _parse_yaml(text: str) -> dict:
    """最小 YAML 子集 parser。
    支持：
        - key: value
        - 嵌套 key:（嵌套 dict）
        - 行内 list [a, b, c]
        - 块 list "- item"
        - YAML block scalar `key: |` 多行字符串（literal）
        - YAML folded scalar `key: >` 多行字符串（fold 为单行，简化处理）
    """
    raw_lines = text.splitlines()  # 保留所有行（不过滤空行，block scalar 需要）
    # 先收集"有效行"列表，但记录原始 index，便于 block scalar 抓取
    root: dict = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        # 跳过空行 / 注释行
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        line = raw.rstrip()
        kv = _KV_PATTERN.match(line)
        lst = _LIST_PATTERN.match(line)

        if kv:
            indent = len(kv.group(1))
            key = kv.group(2)
            raw_val = kv.group(3).strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1] if stack else root
            if not isinstance(parent, dict):
                i += 1
                continue
            # block scalar: `key: |` 或 `key: >`
            if raw_val in ("|", ">", "|-", ">-", "|+", ">+"):
                style = raw_val[0]   # "|" or ">"
                # 收集所有后续比 indent 更深缩进的非空行作为内容
                j = i + 1
                block_lines: list[str] = []
                block_indent: int | None = None
                while j < len(raw_lines):
                    bl = raw_lines[j]
                    if bl.strip() == "":
                        # 空行：保留（literal）/ 视为段落分隔（folded）
                        block_lines.append("")
                        j += 1
                        continue
                    bl_indent = len(bl) - len(bl.lstrip(" "))
                    if bl_indent <= indent:
                        break  # 缩进回到或浅于 key，block 结束
                    if block_indent is None:
                        block_indent = bl_indent
                    # 去掉 block_indent 的前导空格
                    block_lines.append(bl[block_indent:] if bl.startswith(" " * block_indent) else bl.lstrip())
                    j += 1
                # 拼接
                if style == "|":
                    content = "\n".join(block_lines)
                else:  # ">" folded（简化：把单换行变空格，双换行保留）
                    content_parts: list[str] = []
                    buf: list[str] = []
                    for ln in block_lines:
                        if ln == "":
                            if buf:
                                content_parts.append(" ".join(buf))
                                buf = []
                            content_parts.append("")
                        else:
                            buf.append(ln)
                    if buf:
                        content_parts.append(" ".join(buf))
                    content = "\n".join(content_parts)
                # 处理 chomping indicator: "-" 去掉末尾换行；"+" 保留所有
                if raw_val.endswith("-"):
                    content = content.rstrip("\n")
                elif not raw_val.endswith("+"):
                    # 默认 clip：保留单个末尾换行
                    content = content.rstrip("\n") + ("\n" if block_lines and block_lines[-1] != "" else "")
                parent[key] = content
                i = j
                continue
            if raw_val == "":
                # 嵌套 dict 或后续 list
                next_idx = i + 1
                # 跳过空白行 peek 下一有效行
                while next_idx < len(raw_lines) and not raw_lines[next_idx].strip():
                    next_idx += 1
                next_indent = -1
                next_is_list = False
                if next_idx < len(raw_lines):
                    nl = raw_lines[next_idx]
                    next_kv = _KV_PATTERN.match(nl)
                    next_lst = _LIST_PATTERN.match(nl)
                    if next_lst:
                        next_indent = len(next_lst.group(1))
                        next_is_list = next_indent > indent
                    elif next_kv:
                        next_indent = len(next_kv.group(1))
                if next_is_list:
                    container: Any = []
                else:
                    container = {}
                parent[key] = container
                stack.append((indent, container))
            else:
                parent[key] = _parse_scalar(raw_val)
            i += 1
        elif lst:
            indent = len(lst.group(1))
            item_text = lst.group(2).strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1] if stack else root
            if not isinstance(parent, list):
                # 父不是 list（schema 不一致），跳过该行
                i += 1
                continue
            # 列表项可以是 scalar 或 inline map
            if ":" in item_text and _KV_PATTERN.match(item_text):
                # 简化处理：列表项里的 key: value 视为单字段 dict
                m = _KV_PATTERN.match(item_text)
                d: dict = {m.group(2): _parse_scalar(m.group(3).strip())}
                parent.append(d)
                stack.append((indent, d))
            else:
                parent.append(_parse_scalar(item_text))
            i += 1
        else:
            # 不识别的行，跳过
            i += 1
    return root


def _parse_scalar(s: str) -> Any:
    """解析标量：int / float / bool / null / [inline list] / 字符串"""
    if s == "" or s.lower() in ("null", "~"):
        return None
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    # inline list
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x.strip()) for x in _split_csv(inner)]
    # quoted string
    if (s.startswith("\"") and s.endswith("\"")) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # number
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def _split_csv(s: str) -> list[str]:
    """简化的 CSV 分割：尊重 [...] 嵌套和引号"""
    out: list[str] = []
    depth = 0
    in_quote: str | None = None
    cur: list[str] = []
    for ch in s:
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            cur.append(ch)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return [x.strip() for x in out]


# ==================== 内部 dumper ====================

def _dump_yaml(fm: dict, indent: int = 0) -> list[str]:
    out: list[str] = []
    pad = "  " * indent
    for key, val in fm.items():
        if isinstance(val, dict):
            out.append(f"{pad}{key}:")
            out.extend(_dump_yaml(val, indent + 1))
        elif isinstance(val, list):
            if not val:
                out.append(f"{pad}{key}: []")
            elif all(_is_scalar_for_inline(x) for x in val):
                # inline list 形式
                items = ", ".join(_dump_scalar(x) for x in val)
                out.append(f"{pad}{key}: [{items}]")
            else:
                out.append(f"{pad}{key}:")
                for item in val:
                    if isinstance(item, dict):
                        # 块列表 + 嵌套 dict
                        sub = _dump_yaml(item, indent + 1)
                        if sub:
                            first, *rest = sub
                            out.append(f"{pad}- {first.lstrip()}")
                            out.extend(rest)
                    else:
                        out.append(f"{pad}- {_dump_scalar(item)}")
        else:
            out.append(f"{pad}{key}: {_dump_scalar(val)}")
    return out


def _is_scalar_for_inline(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) or v is None


def _dump_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # 需要引号的情况：含 : / # / 开头看起来像数字 / 含特殊字符
        if v == "":
            return '""'
        # 简化判断：如果是纯字母数字 + 常见符号，无需引号
        if re.match(r"^[A-Za-z0-9_./\-+@%]+$", v):
            return v
        # 否则用双引号包，转义内部双引号
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(v)
