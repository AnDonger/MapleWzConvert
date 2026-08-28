"""Replace English XML string values with matching Chinese XML values.

Usage:

    python en_to_cn_xml.py ./en_xml ./cn_xml ./out_en_to_cn_xml

The output keeps the English XML file as the base file. Only the text inside a
matched ``<string ... value="...">`` attribute is replaced; formatting,
newlines, empty-tag style, attribute order, and unrelated fields are preserved.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from html import unescape as html_unescape
from pathlib import Path, PurePosixPath
import re
import shutil
import sys


MISSING_REPORT_NAME = "missing_cn_string_values.txt"

StringKey = tuple[tuple[str, ...], str]

TAG_RE = re.compile(r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![^>]*>|</\s*([A-Za-z_][\w:.-]*)\s*>|<\s*([A-Za-z_][\w:.-]*)\b[^>]*?>", re.DOTALL)
ATTR_RE = re.compile(r"([A-Za-z_][\w:.-]*)\s*=\s*('([^']*)'|\"([^\"]*)\")", re.DOTALL)
XML_DECL_ENCODING_RE = re.compile(br"<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
SKILL_HINT_RE = re.compile(r"h\d+\Z", re.IGNORECASE)
SKILL_DESC_MAX_CHARS = 17
SKILL_DESC_EARLY_BREAK_CHARS = 12
SKILL_DESC_BREAK_CHARS = "，。；：、！？,.;:!?"


@dataclass
class ReplacementStats:
    files_total: int = 0
    files_written: int = 0
    files_missing_cn: int = 0
    files_parse_failed: int = 0
    strings_seen: int = 0
    strings_replaced: int = 0
    strings_missing: int = 0
    strings_skipped_non_han: int = 0
    skill_values_adjusted: int = 0


@dataclass(frozen=True)
class StringSite:
    key: StringKey
    imgdir_path: tuple[str, ...]
    string_name: str
    value: str
    value_span: tuple[int, int]
    quote: str


def _iter_xml_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.xml")
        if path.is_file() and "_logs" not in path.parts
    )


def _relative_posix(root: Path, path: Path) -> str:
    return PurePosixPath(*path.relative_to(root).parts).as_posix()


def _wz_name_from_relative(relative_path: str) -> str:
    parts = PurePosixPath(relative_path).parts
    return parts[0] if parts else "(unknown.wz)"


def _imgdir_line(name: str) -> str:
    return f'<imgdir name="{name}">'


def _contains_han(value: str) -> bool:
    return HAN_RE.search(value) is not None


def _is_skill_xml(relative_path: str) -> bool:
    return PurePosixPath(relative_path).name.lower() == "skill.img.xml"


def _is_cjk_display_char(char: str) -> bool:
    return ord(char) > 127


def _find_desc_punctuation_break(segment: str) -> int | None:
    candidate: int | None = None
    for index, char in enumerate(segment):
        if index + 1 > SKILL_DESC_MAX_CHARS:
            break
        if char in SKILL_DESC_BREAK_CHARS and segment[index + 1 :].strip():
            break_at = index + 1
            remaining_chars = len(segment[break_at:])
            if (
                break_at < SKILL_DESC_EARLY_BREAK_CHARS
                and remaining_chars > SKILL_DESC_EARLY_BREAK_CHARS
            ):
                continue
            hard_line_count = (len(segment) + SKILL_DESC_MAX_CHARS - 1) // SKILL_DESC_MAX_CHARS
            punctuation_line_count = 1 + (
                (remaining_chars + SKILL_DESC_MAX_CHARS - 1) // SKILL_DESC_MAX_CHARS
            )
            if punctuation_line_count <= hard_line_count:
                candidate = break_at
    return candidate


def _find_desc_width_break(segment: str) -> int:
    return min(SKILL_DESC_MAX_CHARS, len(segment))


def _wrap_skill_desc_segment(segment: str) -> str:
    if len(segment) <= SKILL_DESC_MAX_CHARS:
        return segment

    parts: list[str] = []
    rest = segment
    while len(rest) > SKILL_DESC_MAX_CHARS:
        break_at = _find_desc_punctuation_break(rest)
        if break_at is None:
            break_at = _find_desc_width_break(rest)
        if break_at <= 0 or break_at >= len(rest):
            break
        parts.append(rest[:break_at])
        rest = rest[break_at:]

    parts.append(rest)
    return r"\n".join(parts)


def _wrap_skill_desc(value: str) -> str:
    return r"\n".join(_wrap_skill_desc_segment(segment) for segment in value.split(r"\n"))


def _pad_skill_hint_by_cjk_count(value: str) -> str:
    text = value.rstrip(" ")
    pad_count = sum(1 for char in text if _is_cjk_display_char(char))
    if pad_count == 0:
        return text
    return text + (" " * pad_count)


def _adjust_skill_value(site: StringSite, value: str, relative_path: str) -> str:
    if not _is_skill_xml(relative_path) or not _contains_han(value):
        return value
    if site.string_name == "desc":
        return _wrap_skill_desc(value)
    if SKILL_HINT_RE.fullmatch(site.string_name):
        return _pad_skill_hint_by_cjk_count(value)
    return value


def _report_imgdir_name(imgdir_path: tuple[str, ...]) -> str | None:
    if not imgdir_path:
        return None
    if len(imgdir_path) >= 2 and imgdir_path[0].lower().endswith(".img"):
        return imgdir_path[1]
    return imgdir_path[0]


def _detect_xml_encoding(data: bytes) -> tuple[str, bool]:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", True
    match = XML_DECL_ENCODING_RE.search(data[:256])
    if match:
        return match.group(1).decode("ascii", errors="replace"), False
    return "utf-8", False


def _read_xml_text(path: Path) -> tuple[str, str, bool]:
    data = path.read_bytes()
    encoding, has_bom = _detect_xml_encoding(data)
    try:
        return data.decode(encoding), encoding, has_bom
    except UnicodeDecodeError:
        return data.decode("utf-8-sig"), "utf-8-sig", data.startswith(b"\xef\xbb\xbf")


def _write_preserving_encoding(path: Path, text: str, encoding: str, has_bom: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output_encoding = "utf-8-sig" if has_bom else encoding
    path.write_bytes(text.encode(output_encoding, errors="xmlcharrefreplace"))


def _xml_attr_unescape(value: str) -> str:
    return html_unescape(value)


def _xml_attr_escape(value: str, quote: str) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;")
    if quote == '"':
        escaped = escaped.replace('"', "&quot;")
    else:
        escaped = escaped.replace("'", "&apos;")
    return escaped


def _parse_attrs(tag_text: str, absolute_start: int = 0) -> dict[str, tuple[str, tuple[int, int], str]]:
    attrs: dict[str, tuple[str, tuple[int, int], str]] = {}
    for match in ATTR_RE.finditer(tag_text):
        name = match.group(1)
        quote = tag_text[match.start(2)]
        if quote == "'":
            raw_value = match.group(3) or ""
            value_start = match.start(3)
            value_end = match.end(3)
        else:
            raw_value = match.group(4) or ""
            value_start = match.start(4)
            value_end = match.end(4)
        attrs[name] = (
            _xml_attr_unescape(raw_value),
            (absolute_start + value_start, absolute_start + value_end),
            quote,
        )
    return attrs


def _is_self_closing(tag_text: str) -> bool:
    stripped = tag_text.rstrip()
    return len(stripped) >= 2 and stripped[-2] == "/"


def _scan_string_sites(text: str) -> list[StringSite]:
    stack: list[str] = []
    sites: list[StringSite] = []

    for match in TAG_RE.finditer(text):
        tag_text = match.group(0)
        close_name = match.group(1)
        open_name = match.group(2)

        if close_name:
            if close_name == "imgdir" and stack:
                stack.pop()
            continue

        if not open_name:
            continue

        attrs = _parse_attrs(tag_text, match.start())
        if open_name == "imgdir":
            name_value = attrs.get("name")
            if name_value is not None and not _is_self_closing(tag_text):
                stack.append(name_value[0])
            continue

        if open_name != "string":
            continue

        name_value = attrs.get("name")
        value_value = attrs.get("value")
        if name_value is None or value_value is None:
            continue

        string_name = name_value[0]
        value, span, quote = value_value
        imgdir_path = tuple(stack)
        sites.append(
            StringSite(
                key=(imgdir_path, string_name),
                imgdir_path=imgdir_path,
                string_name=string_name,
                value=value,
                value_span=span,
                quote=quote,
            )
        )

    return sites


def _load_cn_values(cn_xml_path: Path) -> dict[StringKey, str]:
    cn_text, _encoding, _has_bom = _read_xml_text(cn_xml_path)
    values: dict[StringKey, str] = {}
    for site in _scan_string_sites(cn_text):
        values[site.key] = site.value
    return values


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _record_missing_imgdir(
    missing: dict[str, dict[str, set[str]]],
    relative_path: str,
    imgdir_path: tuple[str, ...],
) -> None:
    imgdir_name = _report_imgdir_name(imgdir_path)
    if imgdir_name is None:
        return
    wz_name = _wz_name_from_relative(relative_path)
    missing[wz_name][relative_path].add(_imgdir_line(imgdir_name))


def _apply_replacements(text: str, replacements: list[tuple[tuple[int, int], str]]) -> str:
    for (start, end), value in sorted(replacements, key=lambda item: item[0][0], reverse=True):
        text = text[:start] + value + text[end:]
    return text


def replace_file(
    en_xml_path: Path,
    cn_xml_path: Path,
    output_xml_path: Path,
    *,
    en_root: Path,
    missing: dict[str, dict[str, set[str]]],
    stats: ReplacementStats,
) -> None:
    relative_path = _relative_posix(en_root, en_xml_path)

    if not cn_xml_path.is_file():
        stats.files_missing_cn += 1
        en_text, _en_encoding, _en_has_bom = _read_xml_text(en_xml_path)
        for site in _scan_string_sites(en_text):
            stats.strings_seen += 1
            stats.strings_missing += 1
            _record_missing_imgdir(missing, relative_path, site.imgdir_path)
        _copy_file(en_xml_path, output_xml_path)
        return

    try:
        cn_values = _load_cn_values(cn_xml_path)
        en_text, en_encoding, en_has_bom = _read_xml_text(en_xml_path)
        en_sites = _scan_string_sites(en_text)
    except Exception as exc:  # noqa: BLE001
        stats.files_parse_failed += 1
        _copy_file(en_xml_path, output_xml_path)
        print(f"WARNING: XML scan failed, copied English file: {relative_path}: {exc}", file=sys.stderr)
        return

    changed = False
    replacements: list[tuple[tuple[int, int], str]] = []
    for site in en_sites:
        stats.strings_seen += 1
        if site.key not in cn_values:
            stats.strings_missing += 1
            _record_missing_imgdir(missing, relative_path, site.imgdir_path)
            continue

        new_value = cn_values[site.key]
        if not _contains_han(new_value):
            stats.strings_skipped_non_han += 1
            stats.strings_missing += 1
            _record_missing_imgdir(missing, relative_path, site.imgdir_path)
            continue

        adjusted_value = _adjust_skill_value(site, new_value, relative_path)
        if adjusted_value != new_value:
            stats.skill_values_adjusted += 1
            new_value = adjusted_value

        if site.value != new_value:
            replacements.append((site.value_span, _xml_attr_escape(new_value, site.quote)))
            changed = True
            stats.strings_replaced += 1

    if changed:
        _write_preserving_encoding(
            output_xml_path,
            _apply_replacements(en_text, replacements),
            en_encoding,
            en_has_bom,
        )
    else:
        _copy_file(en_xml_path, output_xml_path)


def write_missing_report(output_dir: Path, missing: dict[str, dict[str, set[str]]]) -> Path:
    report_path = output_dir / MISSING_REPORT_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if missing:
        for wz_name in sorted(missing):
            lines.append(wz_name)
            for relative_path in sorted(missing[wz_name]):
                lines.append(f"\t{relative_path}")
                for imgdir_line in sorted(missing[wz_name][relative_path]):
                    lines.append(f"\t\t{imgdir_line}")
                lines.append("")
    else:
        lines.append("未发现缺失项。")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return report_path


def convert_directory(en_dir: Path, cn_dir: Path, output_dir: Path) -> ReplacementStats:
    if not en_dir.is_dir():
        raise SystemExit(f"英文版目录不存在或不是目录: {en_dir}")
    if not cn_dir.is_dir():
        raise SystemExit(f"中文版目录不存在或不是目录: {cn_dir}")

    xml_files = _iter_xml_files(en_dir)
    if not xml_files:
        raise SystemExit(f"英文版目录下没有找到 .xml 文件: {en_dir}")

    stats = ReplacementStats(files_total=len(xml_files))
    missing: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    print(f"English XML: {en_dir}")
    print(f"Chinese XML: {cn_dir}")
    print(f"Output: {output_dir}")
    print(f"XML files: {len(xml_files)}")

    for index, en_xml_path in enumerate(xml_files, 1):
        relative = en_xml_path.relative_to(en_dir)
        cn_xml_path = cn_dir / relative
        output_xml_path = output_dir / relative
        try:
            replace_file(
                en_xml_path,
                cn_xml_path,
                output_xml_path,
                en_root=en_dir,
                missing=missing,
                stats=stats,
            )
            stats.files_written += 1
            print(f"  [{index:>5}/{len(xml_files)}] {PurePosixPath(*relative.parts)}")
        except Exception as exc:  # noqa: BLE001
            stats.files_parse_failed += 1
            _copy_file(en_xml_path, output_xml_path)
            print(f"  [{index:>5}/{len(xml_files)}] {PurePosixPath(*relative.parts)}: ERROR", file=sys.stderr)
            print(f"WARNING: convert failed, copied English file: {PurePosixPath(*relative.parts)}: {exc}", file=sys.stderr)

    report_path = write_missing_report(output_dir, missing)
    print(f"Done. Written: {stats.files_written}")
    print(
        "Strings seen: "
        f"{stats.strings_seen}, replaced: {stats.strings_replaced}, "
        f"missing/skipped: {stats.strings_missing}, skipped non-Han CN: {stats.strings_skipped_non_han}, "
        f"Skill.img.xml adjusted: {stats.skill_values_adjusted}"
    )
    print(f"Missing report: {report_path}")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace English WZ XML string values with matching Chinese WZ XML string values."
    )
    parser.add_argument("en_xml_dir", help="English XML root directory, for example ./en_xml")
    parser.add_argument("cn_xml_dir", help="Chinese XML root directory, for example ./cn_xml")
    parser.add_argument("output_dir", help="Output directory, for example ./out_en_to_cn_xml")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    en_dir = Path(args.en_xml_dir).expanduser().resolve()
    cn_dir = Path(args.cn_xml_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    convert_directory(en_dir, cn_dir, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
