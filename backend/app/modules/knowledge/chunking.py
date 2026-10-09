"""项目侧文本切片，所有输出均不超过 collection 的字符数上限。"""

from __future__ import annotations

import re
from typing import Any

def split_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """按段落优先切片，超长段落再按句子/字符切分，并添加字符级重叠。

    `chunk_size` 和 `overlap` 均以 Python Unicode 字符计数；函数会拒绝无效配置，
    并保证拼接输出覆盖清理后的完整正文。
    """

    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size 必须大于 0，overlap 必须小于 chunk_size")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        hard_end = end
        if end < len(normalized):
            floor = start + max(1, chunk_size // 2)
            boundaries = [
                normalized.rfind(separator, floor, end)
                for separator in ("\n\n", "\n", "。", "！", "？", ". ", " ")
            ]
            boundary = max(boundaries)
            if boundary >= floor:
                end = min(hard_end, boundary + (2 if normalized.startswith("\n\n", boundary) else 1))
        piece = normalized[start:end]
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        next_start = max(start + 1, end - overlap)
        start = next_start
    return chunks


def split_with_rule(text: str, *, chunk_size: int, rule: dict[str, Any] | None) -> list[str]:
    """Apply the validated project chunking rule while respecting Gateway's record limit.

    Delimiters are literal strings, retained at the end of the segment they separate.
    Oversized delimiter segments fall back to the existing bounded fixed splitter.
    """
    config = dict(rule or {})
    strategy = config.get("strategy", "structure")
    configured_size = config.get("chunk_size", chunk_size)
    overlap = config.get("overlap", min(120, max(0, configured_size // 5)))
    if strategy not in {"structure", "fixed", "delimiter"}:
        raise ValueError("strategy 只能是 structure、fixed 或 delimiter。")
    if not isinstance(configured_size, int) or isinstance(configured_size, bool):
        raise ValueError("chunk_size 必须是整数。")
    if configured_size < 1 or configured_size > chunk_size:
        raise ValueError(f"切片长度必须在 1 到 Gateway 上限 {chunk_size} 之间。")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0 or overlap >= configured_size:
        raise ValueError("overlap 必须是非负整数且小于 chunk_size。")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if strategy == "structure":
        return split_structure_first(
            normalized,
            chunk_size=configured_size,
            overlap=overlap,
            markdown=bool(config.get("markdown", False)),
        )
    if strategy == "fixed":
        return split_text(normalized, chunk_size=configured_size, overlap=overlap)

    delimiter = config.get("delimiter")
    if not isinstance(delimiter, str) or not delimiter:
        raise ValueError("自定义分隔符不能为空。")
    delimiter = delimiter.replace("\\n", "\n").replace("\\t", "\t")
    raw_parts = normalized.split(delimiter)
    segments = [part + delimiter for part in raw_parts[:-1]]
    if raw_parts[-1]:
        segments.append(raw_parts[-1])
    chunks: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if len(segment) <= configured_size:
            chunks.append(segment)
        else:
            chunks.extend(split_text(segment, chunk_size=configured_size, overlap=overlap))
    return chunks


def _markdown_sections(text: str) -> list[dict[str, Any]]:
    """Split Markdown at ATX headings, ignoring heading-looking lines in fenced code."""
    sections: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    body: list[str] = []
    current_level: int | None = None
    fence_char: str | None = None
    fence_size = 0

    def flush() -> None:
        value = "\n".join(body).strip()
        if value or heading_stack or not sections:
            sections.append({
                "heading_path": [heading for _, heading in heading_stack],
                "heading_level": current_level,
                "body": value,
            })
        body.clear()

    lines = text.splitlines()
    for index, line in enumerate(lines):
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if fence_char is None:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = None, 0
            body.append(line)
            continue
        heading_match = None if fence_char else re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, line.strip()))
            current_level = level
            continue
        body.append(line)
    flush()
    return sections


def _paragraph_blocks(text: str, *, markdown: bool) -> list[str]:
    """Keep fenced code blocks intact; otherwise split on blank lines."""
    if not markdown:
        return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    blocks: list[str] = []
    buffer: list[str] = []
    in_fence = False
    fence_char = ""
    fence_size = 0

    def flush() -> None:
        value = "\n".join(buffer).strip()
        if value:
            blocks.append(value)
        buffer.clear()

    lines = text.splitlines()
    for index, line in enumerate(lines):
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                flush()
                in_fence, fence_char, fence_size = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                in_fence = False
            buffer.append(line)
            if not in_fence:
                flush()
            continue
        if not in_fence and not line.strip():
            lookahead = index + 1
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            next_line = lines[lookahead] if lookahead < len(lines) else ""
            buffer_is_list = any(
                re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", item) for item in buffer
            )
            next_is_list = bool(re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", next_line))
            if buffer_is_list and next_is_list:
                buffer.append("")
            else:
                flush()
        else:
            buffer.append(line)
    flush()
    return blocks


def _section_payload(section: dict[str, Any]) -> tuple[str, str]:
    heading_path = section["heading_path"]
    prefix = "\n".join(heading_path)
    body = section["body"]
    return prefix, body


def _bounded_heading(prefix: str, chunk_size: int) -> str:
    limit = max(0, chunk_size // 3)
    if len(prefix) <= limit:
        return prefix
    return prefix[:limit]


def split_structure_first(
    text: str, *, chunk_size: int, overlap: int, markdown: bool
) -> list[str]:
    """Split headings/paragraphs first, then sentences and characters as a fallback."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    sections = _markdown_sections(normalized) if markdown else [{
        "heading_path": [], "heading_level": None, "body": normalized,
    }]
    output: list[str] = []
    current = ""
    for section in sections:
        prefix, body = _section_payload(section)
        prefix = _bounded_heading(prefix, chunk_size)
        available = max(1, chunk_size - len(prefix) - (1 if prefix else 0))
        section_overlap = min(overlap, max(0, available - 1))
        blocks = _paragraph_blocks(body, markdown=markdown)
        if not blocks and prefix:
            blocks = [""]
        body_chunks = split_text(
            "\n\n".join(blocks), chunk_size=available, overlap=section_overlap
        ) if blocks else []
        if not body_chunks:
            continue
        for body_chunk in body_chunks:
            unit = f"{prefix}\n{body_chunk}" if prefix and body_chunk else prefix or body_chunk
            if len(unit) > chunk_size:
                unit = unit[-chunk_size:]
            if current and len(current) + 2 + len(unit) <= chunk_size:
                current = f"{current}\n\n{unit}"
            else:
                if current:
                    output.append(current)
                current = unit
    if current:
        output.append(current)
    return output


def split_parent_groups(
    text: str, *, parent_chunk_size: int, markdown: bool, parent_heading_level: int
) -> list[str]:
    """Build bounded parent contexts from Markdown sections or plain-text paragraphs."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if not markdown:
        blocks = _paragraph_blocks(normalized, markdown=False)
        groups: list[str] = []
        current = ""
        for block in blocks:
            parts = split_text(block, chunk_size=parent_chunk_size, overlap=0) if len(block) > parent_chunk_size else [block]
            for part in parts:
                candidate = f"{current}\n\n{part}" if current else part
                if current and len(candidate) > parent_chunk_size:
                    groups.append(current)
                    current = part
                else:
                    current = candidate
        if current:
            groups.append(current)
        return groups

    sections = _markdown_sections(normalized)
    raw_groups: list[str] = []
    current_sections: list[dict[str, Any]] = []
    for section in sections:
        level = section.get("heading_level")
        is_boundary = level is not None and level <= parent_heading_level
        if is_boundary and current_sections:
            raw_groups.append("\n\n".join(
                f"{prefix}\n{body}".strip() if prefix and body else prefix or body
                for prefix, body in map(_section_payload, current_sections)
            ).strip())
            current_sections = []
        current_sections.append(section)
    if current_sections:
        raw_groups.append("\n\n".join(
            f"{prefix}\n{body}".strip() if prefix and body else prefix or body
            for prefix, body in map(_section_payload, current_sections)
        ).strip())

    groups = []
    for group in raw_groups:
        if not group:
            continue
        if len(group) <= parent_chunk_size:
            groups.append(group)
        else:
            groups.extend(split_structure_first(
                group, chunk_size=parent_chunk_size, overlap=0, markdown=True
            ))
    return groups


def split_document_with_rule(
    text: str, *, chunk_size: int, rule: dict[str, Any] | None, markdown: bool
) -> list[dict[str, Any]]:
    """Return child chunks and optional local-only parent context for each child."""
    config = dict(rule or {})
    if not config.get("hierarchy_enabled"):
        return [
            {"content": piece, "parent_index": None, "parent_content": None}
            for piece in split_with_rule(text, chunk_size=chunk_size, rule={**config, "markdown": markdown})
        ]

    parent_size = config.get("parent_chunk_size", 4000)
    heading_level = config.get("parent_heading_level", 2)
    if not isinstance(parent_size, int) or isinstance(parent_size, bool) or not 1 <= parent_size <= 20000:
        raise ValueError("parent_chunk_size 必须在 1 到 20000 之间。")
    if not isinstance(heading_level, int) or isinstance(heading_level, bool) or not 1 <= heading_level <= 6:
        raise ValueError("parent_heading_level 必须在 1 到 6 之间。")
    parents = split_parent_groups(
        text,
        parent_chunk_size=parent_size,
        markdown=markdown,
        parent_heading_level=heading_level,
    )
    results: list[dict[str, Any]] = []
    for parent_index, parent in enumerate(parents):
        effective = dict(config)
        effective["hierarchy_enabled"] = False
        configured_size = effective.get("chunk_size", chunk_size)
        heading = ""
        if markdown:
            match = re.search(r"^\s{0,3}#{1,6}\s+.+$", parent, flags=re.MULTILINE)
            if match:
                heading = _bounded_heading(match.group(0).strip(), configured_size)
        child_size = max(1, configured_size - len(heading) - (1 if heading else 0))
        effective["chunk_size"] = child_size
        effective["overlap"] = min(effective.get("overlap", 0), max(0, child_size - 1))
        child_source = parent[len(heading):].lstrip() if heading and parent.startswith(heading) else parent
        pieces = split_with_rule(
            child_source, chunk_size=chunk_size, rule={**effective, "markdown": markdown}
        )
        if not pieces and heading:
            pieces = [""]
        for piece in pieces:
            content = f"{heading}\n{piece}" if heading and piece else heading or piece
            results.append({"content": content, "parent_index": parent_index, "parent_content": parent})
    return results
