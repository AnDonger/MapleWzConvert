"""Write a PNG image into one canvas node in an exported .img.xml file."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import re
import sys
from typing import Any
from xml.parsers import expat

from PIL import Image

from wz_to_xml import _load_wzpy


class CanvasLocator:
    def __init__(self, node_path: str):
        self.parts = _split_node_path(node_path)
        self.stack: list[tuple[str, str | None]] = []
        self.match: dict[str, Any] | None = None

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        name = attrs.get("name")
        self.stack.append((tag, name))
        if self.match is not None or tag.lower() != "canvas":
            return

        path = [item_name for _item_tag, item_name in self.stack if item_name is not None]
        if path == self.parts or path[1:] == self.parts:
            self.match = {
                "byte_index": self.parser.CurrentByteIndex,
                "attrs": dict(attrs),
                "path": "/".join(path),
            }

    def end(self, _tag: str) -> None:
        self.stack.pop()


def _split_node_path(node_path: str) -> list[str]:
    parts = [part for part in node_path.replace("\\", "/").split("/") if part]
    if not parts:
        raise ValueError("node path is empty")
    return parts


def _int_attr(attrs: dict[str, str], name: str) -> int:
    value = attrs.get(name)
    if value in (None, ""):
        raise ValueError(f"canvas missing {name!r} attribute")
    return int(value)


def _is_listwz_payload(raw: bytes) -> bool:
    if len(raw) < 2:
        return False
    return int.from_bytes(raw[:2], "little") not in {0x9C78, 0xDA78, 0x0178, 0x5E78}


def _find_canvas(data: bytes, node_path: str) -> dict[str, Any]:
    locator = CanvasLocator(node_path)
    parser = expat.ParserCreate()
    locator.parser = parser
    parser.StartElementHandler = locator.start
    parser.EndElementHandler = locator.end
    parser.Parse(data, True)
    if locator.match is None:
        raise ValueError(f"canvas node not found: {node_path}")
    return locator.match


def _find_start_tag_end(data: bytes, start: int) -> int:
    quote: int | None = None
    index = start
    while index < len(data):
        char = data[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in (ord('"'), ord("'")):
            quote = char
        elif char == ord(">"):
            return index + 1
        index += 1
    raise ValueError("target canvas start tag is not closed")


def _replace_or_insert_attr(tag: bytes, name: str, value: str) -> bytes:
    pattern = re.compile(
        rb"(\s" + re.escape(name.encode("ascii")) + rb"\s*=\s*)([\"'])(.*?)(\2)",
        re.DOTALL,
    )
    value_bytes = value.encode("ascii")

    def repl(match: re.Match[bytes]) -> bytes:
        return match.group(1) + match.group(2) + value_bytes + match.group(4)

    updated, count = pattern.subn(repl, tag, count=1)
    if count:
        return updated

    insert_at = tag.rfind(b"/>")
    if insert_at == -1:
        insert_at = tag.rfind(b">")
    if insert_at == -1:
        raise ValueError("target canvas start tag is not closed")
    return tag[:insert_at] + f' {name}="{value}"'.encode("ascii") + tag[insert_at:]


def _replace_canvas_attrs(
    data: bytes,
    start: int,
    width: int,
    height: int,
    raw_payload: bytes,
) -> bytes:
    end = _find_start_tag_end(data, start)
    tag = data[start:end]
    rawdata = base64.b64encode(raw_payload).decode("ascii")
    tag = _replace_or_insert_attr(tag, "width", str(width))
    tag = _replace_or_insert_attr(tag, "height", str(height))
    tag = _replace_or_insert_attr(tag, "rawlength", str(len(raw_payload)))
    tag = _replace_or_insert_attr(tag, "rawdata", rawdata)
    return data[:start] + tag + data[end:]


def write_png_to_xml_canvas(
    png_path: Path,
    xml_path: Path,
    node_path: str,
    *,
    region: str,
    zlib_level: int,
) -> tuple[int, int, int]:
    from wzpy.canvas import encode_canvas_payload
    from wzpy.crypto import WzKey

    data = xml_path.read_bytes()
    match = _find_canvas(data, node_path)
    attrs = match["attrs"]
    fmt = _int_attr(attrs, "format") + (_int_attr(attrs, "format2") << 8)

    image = Image.open(png_path).convert("RGBA")
    width, height = image.size

    old_raw = base64.b64decode(attrs.get("rawdata", "").encode("ascii")) if attrs.get("rawdata") else b""
    raw_payload = encode_canvas_payload(
        image,
        fmt,
        width,
        height,
        key=WzKey.for_region(region),
        listwz=_is_listwz_payload(old_raw),
        zlib_level=zlib_level,
    )
    xml_path.write_bytes(
        _replace_canvas_attrs(data, int(match["byte_index"]), width, height, raw_payload)
    )
    return width, height, len(raw_payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a PNG into one canvas node in .img.xml."
    )
    parser.add_argument("png_file", help="Input .png file path.")
    parser.add_argument("xml_file", help="Target .img.xml file path to modify in place.")
    parser.add_argument("node", help="Canvas node path, for example: Title/logo/0/0")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument(
        "--zlib-level",
        type=int,
        default=6,
        choices=range(0, 10),
        metavar="0-9",
        help="zlib compression level. Default: 6.",
    )
    parser.add_argument(
        "--wzpy-path",
        type=Path,
        help="Deprecated; ignored. The project-local maplewz_sdk is used.",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Deprecated; ignored. The project-local maplewz_sdk is used.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _load_wzpy(args.wzpy_path, args.no_bootstrap)

    png_path = Path(args.png_file).expanduser().resolve()
    xml_path = Path(args.xml_file).expanduser().resolve()
    if not png_path.is_file():
        raise SystemExit(f"input PNG file does not exist: {png_path}")
    if not xml_path.is_file():
        raise SystemExit(f"target XML file does not exist: {xml_path}")

    try:
        width, height, raw_length = write_png_to_xml_canvas(
            png_path,
            xml_path,
            args.node,
            region=args.region,
            zlib_level=args.zlib_level,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should show a concise error.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote canvas: {args.node} ({width}x{height}, rawlength={raw_length})")
    print(f"XML: {xml_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
