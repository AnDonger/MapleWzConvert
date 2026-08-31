"""Export canvas rawdata nodes from exported .img.xml files to PNG."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
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
    safe_name = _safe_png_stem(_split_node_path(node_path))
    return output / f"{safe_name}.png"


def _safe_png_stem(parts: list[str]) -> str:
    safe_name = "_".join(parts)
    for char in '<>:"/\\|?*':
        safe_name = safe_name.replace(char, "_")
    safe_name = safe_name.strip(" ._")
    return safe_name or "canvas"


def _unique_png_path(output_dir: Path, parts: list[str], used_paths: set[Path]) -> Path:
    stem = _safe_png_stem(parts)
    candidate = output_dir / f"{stem}.png"
    if candidate not in used_paths and not candidate.exists():
        used_paths.add(candidate)
        return candidate

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    duplicate_stem = f"{stem}_重复_{timestamp}"
    candidate = output_dir / f"{duplicate_stem}.png"
    index = 2
    while candidate in used_paths or candidate.exists():
        candidate = output_dir / f"{duplicate_stem}_{index}.png"
        index += 1
    used_paths.add(candidate)
    return candidate


def _canvas_to_namespace(canvas_element: ET.Element) -> SimpleNamespace:
    rawdata = canvas_element.get("rawdata")
    if not rawdata:
        raise ValueError("canvas has no rawdata")
    return SimpleNamespace(
        width=_int_attr(canvas_element, "width"),
        height=_int_attr(canvas_element, "height"),
        format=_int_attr(canvas_element, "format"),
        format2=_int_attr(canvas_element, "format2"),
        _png_data=base64.b64decode(rawdata.encode("ascii")),
    )


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

    canvas = _canvas_to_namespace(canvas_element)

    png_path = _default_png_path(output, node_path).resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image = decode_canvas(canvas, region=region)
    image.save(png_path)
    return png_path


def _iter_canvas_nodes(
    element: ET.Element,
    path_parts: list[str],
) -> list[tuple[list[str], ET.Element]]:
    results: list[tuple[list[str], ET.Element]] = []
    for child in list(element):
        child_name = child.get("name", child.tag)
        child_path = [*path_parts, child_name]
        if child.tag.lower() == "canvas":
            results.append((child_path, child))
        results.extend(_iter_canvas_nodes(child, child_path))
    return results


def export_all_canvases_to_png(
    xml_path: Path,
    output_dir: Path,
    *,
    region: str,
) -> tuple[int, list[str]]:
    from wzpy.canvas import decode_canvas

    root = ET.parse(xml_path).getroot()
    canvas_nodes = _iter_canvas_nodes(root, [])
    if not canvas_nodes:
        return 0, []

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    exported = 0
    failures: list[str] = []
    used_paths: set[Path] = set()
    for parts, canvas_element in canvas_nodes:
        node_path = "/".join(parts)
        try:
            canvas = _canvas_to_namespace(canvas_element)
            png_path = _unique_png_path(output_dir, parts, used_paths)
            image = decode_canvas(canvas, region=region)
            image.save(png_path)
            exported += 1
            print(f"Wrote: {png_path}")
        except Exception as exc:  # noqa: BLE001 - continue exporting the rest.
            failures.append(f"{node_path}: {exc}")
            print(f"ERROR: {node_path}: {exc}", file=sys.stderr)
    return exported, failures


def export_xml_tree_canvases_to_png(
    input_dir: Path,
    output_dir: Path,
    *,
    region: str,
) -> tuple[int, int, list[str]]:
    xml_files = sorted(input_dir.rglob("*.xml"))
    output_dir = output_dir.resolve()

    exported_total = 0
    file_count = 0
    failures: list[str] = []
    for index, xml_path in enumerate(xml_files, start=1):
        rel_path = xml_path.relative_to(input_dir)
        target_dir = output_dir / rel_path.parent / xml_path.name
        print(f"  [{index:>5}/{len(xml_files)}] {rel_path}")
        exported, file_failures = export_all_canvases_to_png(
            xml_path,
            target_dir,
            region=region,
        )
        if exported:
            file_count += 1
            exported_total += exported
        for failure in file_failures:
            failures.append(f"{rel_path}: {failure}")
    return file_count, exported_total, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export canvas rawdata nodes from .img.xml files to PNG."
    )
    parser.add_argument("xml_file", help="Input .img.xml file path or XML directory.")
    parser.add_argument("output", help="Output .png file path or output directory.")
    parser.add_argument("node", nargs="?", help="Canvas node path, for example: Title/logo/0/0")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
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

    input_path = Path(args.xml_file).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"input path does not exist: {input_path}")

    try:
        output = Path(args.output).expanduser()
        if args.node:
            if not input_path.is_file():
                raise ValueError("node path mode requires one input .img.xml file")
            png_path = export_canvas_to_png(
                input_path,
                output,
                args.node,
                region=args.region,
            )
            print(f"Wrote: {png_path}")
            return 0
        if input_path.is_dir():
            file_count, exported, failures = export_xml_tree_canvases_to_png(
                input_path,
                output,
                region=args.region,
            )
            print(
                f"Done. XML files with canvas: {file_count}, "
                f"exported: {exported}, failed: {len(failures)}"
            )
            return 1 if failures else 0
        exported, failures = export_all_canvases_to_png(input_path, output, region=args.region)
    except Exception as exc:  # noqa: BLE001 - CLI should show a concise error.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Exported: {exported}, failed: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
