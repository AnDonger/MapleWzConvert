"""Pack exported XML folders back into a legacy MapleStory .wz file.

Input is usually a folder named like ``Map.wz`` containing files such as:

    Tile/blackTile.img.xml
    Map/Map0/000000000.img.xml

Export metadata attributes on the root ``<imgdir>`` are ignored while packing;
valid XML nodes are rebuilt from their node content so edits are written into
the new WZ.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import io
from pathlib import Path, PurePosixPath
import sys
import threading
from typing import Any
import xml.etree.ElementTree as ET


DEFAULT_COPYRIGHT = "Package file v1.0 Copyright 2002 Wizet, ZMS"


class PackLogger:
    """Write only warning/error information to logs."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"wz_pack_{stamp}.log"
        self.latest_path = logs_dir / "wz_pack_latest.log"
        self._files = [
            self.path.open("w", encoding="utf-8", newline="\n"),
            self.latest_path.open("w", encoding="utf-8", newline="\n"),
        ]
        self.warning_count = 0
        self.error_count = 0

    def close(self) -> None:
        for file in self._files:
            file.close()

    def write(self, level: str, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} [{level}] {message}\n"
        for file in self._files:
            file.write(line)
            file.flush()

    def warning(self, message: str) -> None:
        self.warning_count += 1
        self.write("WARNING", message)

    def error(self, message: str) -> None:
        self.error_count += 1
        self.write("ERROR", message)


class SyntheticWzFile:
    """Small source holder for newly created image/canvas nodes."""

    def __init__(self, reader):
        self.reader = reader
        self.reader_lock = threading.RLock()


def _load_wzpy(wzpy_path: Path | None, no_bootstrap: bool) -> None:
    if wzpy_path is not None or no_bootstrap:
        print("Note: --wzpy-path/--no-bootstrap are ignored; using project-local maplewz_sdk.")
    from maplewz_sdk import ensure_wzpy_importable

    ensure_wzpy_importable()


def _int_attr(element: ET.Element, name: str, default: int = 0) -> int:
    value = element.get(name)
    if value is None or value == "":
        return default
    return int(value)


def _float_attr(element: ET.Element, name: str, default: float = 0.0) -> float:
    value = element.get(name)
    if value is None or value == "":
        return default
    return float(value)


def _canvas_format_attrs(element: ET.Element) -> tuple[int, int]:
    return _int_attr(element, "format"), _int_attr(element, "format2")


def _decode_base64_attr(element: ET.Element, name: str) -> bytes:
    value = element.get(name)
    if not value:
        return b""
    return base64.b64decode(value.encode("ascii"))


def _set_raw_image_body(image: Any, raw_body: bytes) -> None:
    from wzpy.properties import WzSubProperty

    image._raw_body = raw_body
    image.size = len(raw_body)
    image._root = WzSubProperty(image.name)
    image._parsed = True


def _parse_property(
    element: ET.Element,
    parent: Any,
    *,
    image: Any,
    region: str,
    logger: PackLogger,
    context: str,
) -> Any:
    from wzpy.properties import (
        WzCanvasProperty,
        WzConvexProperty,
        WzDoubleProperty,
        WzFloatProperty,
        WzIntProperty,
        WzLongProperty,
        WzNullProperty,
        WzShortProperty,
        WzSoundProperty,
        WzStringProperty,
        WzSubProperty,
        WzUolProperty,
        WzVectorProperty,
    )

    tag = element.tag.lower()
    name = element.get("name", "")

    if tag == "null":
        return WzNullProperty(name, parent)
    if tag == "short":
        return WzShortProperty(name, _int_attr(element, "value"), parent)
    if tag == "int":
        return WzIntProperty(name, _int_attr(element, "value"), parent)
    if tag == "long":
        return WzLongProperty(name, _int_attr(element, "value"), parent)
    if tag == "float":
        return WzFloatProperty(name, _float_attr(element, "value"), parent)
    if tag == "double":
        return WzDoubleProperty(name, _float_attr(element, "value"), parent)
    if tag == "string":
        return WzStringProperty(name, element.get("value", ""), parent)
    if tag == "vector":
        return WzVectorProperty(name, _int_attr(element, "x"), _int_attr(element, "y"), parent)
    if tag == "uol":
        return WzUolProperty(name, element.get("value", element.get("target", "")), parent)

    if tag == "sound":
        prop = WzSoundProperty(name, parent)
        prop.length_ms = _int_attr(element, "length_ms")
        prop.header = _decode_base64_attr(element, "header")
        data = _decode_base64_attr(element, "rawdata")
        prop._data = data
        prop._data_length = len(data)
        return prop

    if tag == "canvas":
        prop = WzCanvasProperty(name, parent)
        raw = _decode_base64_attr(element, "rawdata")
        prop.width = _int_attr(element, "width")
        prop.height = _int_attr(element, "height")
        prop.format, prop.format2 = _canvas_format_attrs(element)
        prop._wz_image = image
        prop._png_data = raw
        prop._png_length = len(raw)

        for child in list(element):
            prop.add(
                _parse_property(
                    child,
                    prop,
                    image=image,
                    region=region,
                    logger=logger,
                    context=f"{context}/{child.get('name', child.tag)}",
                )
            )
        return prop

    if tag == "extended":
        prop = WzConvexProperty(name, parent)
        for index, child in enumerate(list(element)):
            if child.tag.lower() == "vector":
                prop.points.append(
                    WzVectorProperty(
                        child.get("name", str(index)),
                        _int_attr(child, "x"),
                        _int_attr(child, "y"),
                        prop,
                    )
                )
        return prop

    if tag == "imgdir":
        prop = WzSubProperty(name, parent)
        for child in list(element):
            prop.add(
                _parse_property(
                    child,
                    prop,
                    image=image,
                    region=region,
                    logger=logger,
                    context=f"{context}/{child.get('name', child.tag)}",
                )
            )
        return prop

    raise ValueError(f"unsupported XML tag <{element.tag}> at {context}")


def _img_name_from_xml_path(xml_path: Path) -> str:
    name = xml_path.name
    if name.lower().endswith(".xml"):
        return name[:-4]
    return name


def _image_path_from_xml(root_dir: Path, xml_path: Path) -> PurePosixPath:
    rel = xml_path.relative_to(root_dir)
    parts = list(rel.parts)
    parts[-1] = _img_name_from_xml_path(xml_path)
    return PurePosixPath(*parts)


def _find_xml_files(root_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in root_dir.rglob("*.xml")
        if path.is_file() and "_logs" not in path.parts and path.name != "_wz_meta.xml"
    )


def _ensure_directory(root: Any, parts: tuple[str, ...]) -> Any:
    from wzpy.wz_file import WzDirectory

    current = root
    for part in parts:
        if part not in current.subdirs:
            current.subdirs[part] = WzDirectory(part, parent=current)
        current = current.subdirs[part]
    return current


def _load_template_header(template: Path | None, region: str, version: int | None):
    if template is None:
        return None

    from wzpy import WzFile

    with WzFile.open(str(template), region=region, version=version) as wz:
        return wz.header.copyright, wz.header.fstart


def build_wz_from_xml(
    input_dir: Path,
    output_path: Path,
    *,
    region: str,
    version: int,
    copyright: str,
    fstart: int,
    logger: PackLogger,
) -> int:
    from wzpy.crypto import WzKey, compute_version_hash
    from wzpy.reader import WzBinaryReader
    from wzpy.wz_file import WzFile, WzHeader
    from wzpy.wz_image import WzImage

    reader = WzBinaryReader(io.BytesIO(b""), WzKey.for_region(region), header_fstart=fstart)
    reader.version_hash = compute_version_hash(version)

    wz = WzFile(str(output_path), region=region, version=version)
    wz.header = WzHeader(ident="PKG1", fsize=0, fstart=fstart, copyright=copyright)
    wz._reader = reader
    holder = SyntheticWzFile(reader)

    xml_files = _find_xml_files(input_dir)
    if not xml_files:
        raise SystemExit(f"no .xml files found under {input_dir}")

    print(f"XML files: {len(xml_files)}")
    for index, xml_path in enumerate(xml_files, 1):
        image_path = _image_path_from_xml(input_dir, xml_path)
        parent = _ensure_directory(wz.root, image_path.parts[:-1])
        image_name = image_path.name

        try:
            image = WzImage(image_name, parent=parent, offset=0, size=0, wz_file=holder)
            raw_file_bytes = xml_path.read_bytes()
            mode = "rebuilt"
            try:
                element_tree = ET.parse(xml_path)
                root_element = element_tree.getroot()
            except ET.ParseError:
                _set_raw_image_body(image, raw_file_bytes)
                mode = "raw-file"
            else:
                if root_element.tag.lower() != "imgdir":
                    _set_raw_image_body(image, raw_file_bytes)
                    mode = "raw-file"
                else:
                    try:
                        image._root = _parse_property(
                            root_element,
                            None,
                            image=image,
                            region=region,
                            logger=logger,
                            context=str(image_path),
                        )
                        image._root.name = image_name
                        image._parsed = True
                        mode = "rebuilt"
                    except Exception:
                        _set_raw_image_body(image, raw_file_bytes)
                        mode = "raw-file"
            parent.images[image_name] = image
            print(f"  [{index:>5}/{len(xml_files)}] {image_path} [{mode}]")
        except Exception as exc:  # noqa: BLE001 - keep a focused error log.
            logger.error(f"{xml_path}: {exc}")
            raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return wz.save_as(str(output_path))


def default_output_path(input_dir: Path) -> Path:
    if input_dir.name.lower().endswith(".wz"):
        return input_dir.with_suffix(".packed.wz")
    return input_dir.with_suffix(".wz")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack a WZ XML folder, such as Map.wz/, back into a .wz file."
    )
    parser.add_argument("input_dir", help="Folder containing .img.xml files, often named Map.wz.")
    parser.add_argument(
        "output_wz",
        nargs="?",
        help="Output .wz path. Default: <input name>.packed.wz when input ends with .wz.",
    )
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument("--version", type=int, default=83, help="WZ version. Default: 83.")
    parser.add_argument("--copyright", default=DEFAULT_COPYRIGHT, help="WZ copyright string.")
    parser.add_argument("--fstart", type=int, default=60, help="WZ header fstart. Default: 60.")
    parser.add_argument(
        "--template-wz",
        type=Path,
        help="Optional source .wz whose copyright/fstart should be reused.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Directory for warning/error logs. Default: <output folder>/_logs.",
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

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"input is not a directory: {input_dir}")

    output_path = (
        Path(args.output_wz).expanduser().resolve()
        if args.output_wz
        else default_output_path(input_dir).resolve()
    )
    logs_dir = (args.logs_dir or (output_path.parent / "_logs")).expanduser().resolve()
    logger = PackLogger(logs_dir)
    data_size = 0
    exit_code = 0

    try:
        copyright = args.copyright
        fstart = args.fstart
        template = _load_template_header(args.template_wz, args.region, args.version)
        if template is not None:
            copyright, fstart = template

        print(f"Input: {input_dir}")
        print(f"Output: {output_path}")
        print(f"Region: {args.region}, version: {args.version}")
        print(f"fstart: {fstart}")
        try:
            data_size = build_wz_from_xml(
                input_dir,
                output_path,
                region=args.region,
                version=args.version,
                copyright=copyright,
                fstart=fstart,
                logger=logger,
            )
        except Exception as exc:  # noqa: BLE001 - CLI should fail cleanly.
            print(f"ERROR: {exc}", file=sys.stderr)
            if logger.error_count == 0:
                logger.error(str(exc))
            exit_code = 1

        if logger.warning_count or logger.error_count:
            logger.write(
                "SUMMARY",
                f"warnings={logger.warning_count} errors={logger.error_count}",
            )
    finally:
        logger.close()

    if exit_code:
        print("Done. Failed.")
    else:
        print(f"Done. Wrote {data_size} bytes")
    print(f"Log: {logger.path}")
    print(f"Latest log: {logger.latest_path}")
    return 1 if exit_code or logger.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
