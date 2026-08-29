"""Export one canvas rawdata node from an exported .img.xml file to PNG."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from wz_to_xml import _load_wzpy


def _int_attr(element: ET.Element, name: str) -> int:
    value = element.get(name)
    if value in (None, ""):
        raise ValueError(f"canvas missing {name!r} attribute")
    return int(value)


def _split_node_path(node_path: str) -> list[str]:
    parts = [part for part in node_path.replace("\\", "/").split("/") if part]
    if not parts:
        raise ValueError("node path is empty")
    return parts


def _children_named(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if child.get("name") == name]


def _find_node(root: ET.Element, node_path: str) -> ET.Element:
    parts = _split_node_path(node_path)
    if parts and root.get("name") == parts[0]:
        parts = parts[1:]

    current = root
    for part in parts:
        matches = _children_named(current, part)
        if not matches:
            current_name = current.get("name", current.tag)
            raise ValueError(f"node not found under {current_name!r}: {part!r}")
        if len(matches) > 1:
            canvas_matches = [item for item in matches if item.tag.lower() == "canvas"]
            current = canvas_matches[0] if len(canvas_matches) == 1 else matches[0]
        else:
            current = matches[0]
    return current


def _default_png_path(output: Path, node_path: str) -> Path:
    if output.suffix.lower() == ".png":
        return output
    safe_name = "_".join(_split_node_path(node_path))
    for char in '<>:"/\\|?*':
        safe_name = safe_name.replace(char, "_")
    return output / f"{safe_name}.png"


def export_canvas_to_png(
    xml_path: Path,
    output: Path,
    node_path: str,
    *,
    region: str,
) -> Path:
    from wzpy.canvas import decode_canvas

    root = ET.parse(xml_path).getroot()
    canvas_element = _find_node(root, node_path)
    if canvas_element.tag.lower() != "canvas":
        name = canvas_element.get("name", canvas_element.tag)
        raise ValueError(f"target node is not a canvas: {name!r} <{canvas_element.tag}>")

    rawdata = canvas_element.get("rawdata")
    if not rawdata:
        raise ValueError(f"target canvas has no rawdata: {node_path}")

    canvas = SimpleNamespace(
        width=_int_attr(canvas_element, "width"),
        height=_int_attr(canvas_element, "height"),
        format=_int_attr(canvas_element, "format"),
        format2=_int_attr(canvas_element, "format2"),
        _png_data=base64.b64decode(rawdata.encode("ascii")),
    )

    png_path = _default_png_path(output, node_path).resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image = decode_canvas(canvas, region=region)
    image.save(png_path)
    return png_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export one canvas rawdata node from .img.xml to PNG."
    )
    parser.add_argument("xml_file", help="Input .img.xml file path.")
    parser.add_argument("output", help="Output .png file path or output directory.")
    parser.add_argument("node", help="Canvas node path, for example: Title/logo/0/0")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument(
        "--wzpy-path",
        type=Path,
        help="Path to a local clone of https://github.com/Leonana69/wz-python.",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Do not clone .tools/wz-python automatically if wzpy is missing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _load_wzpy(args.wzpy_path, args.no_bootstrap)

    xml_path = Path(args.xml_file).expanduser().resolve()
    if not xml_path.is_file():
        raise SystemExit(f"input XML file does not exist: {xml_path}")

    try:
        png_path = export_canvas_to_png(
            xml_path,
            Path(args.output).expanduser(),
            args.node,
            region=args.region,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should show a concise error.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
