"""Export MapleStory GMS v83 .wz files to XML files.

The script uses the open-source ``wzpy`` reader from:
https://github.com/Leonana69/wz-python

If ``wzpy`` is not already importable, the script can bootstrap it into
``.tools/wz-python`` with git. Install Python dependencies first:

    python -m pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any
from xml.sax.saxutils import quoteattr


WZPY_REPO = "https://github.com/Leonana69/wz-python.git"
DEFAULT_WZPY_DIR = Path(__file__).resolve().parent / ".tools" / "wz-python"


class ExportLogger:
    """Write warning/error logs and keep a latest mirror for the most recent run."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"wz_export_{stamp}.log"
        self.latest_path = logs_dir / "wz_export_latest.log"
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

    def info(self, message: str) -> None:
        print(message)

    def warning(self, message: str) -> None:
        self.warning_count += 1
        self.write("WARNING", message)

    def error(self, message: str) -> None:
        self.error_count += 1
        self.write("ERROR", message)


def _add_wzpy_path(path: Path) -> None:
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _bootstrap_wzpy(path: Path) -> None:
    if (path / "wzpy").is_dir():
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"wzpy not found, cloning {WZPY_REPO} -> {path}")
    subprocess.run(
        ["git", "clone", "--depth", "1", WZPY_REPO, str(path)],
        check=True,
    )


def _load_wzpy(wzpy_path: Path | None, no_bootstrap: bool) -> None:
    if wzpy_path is not None:
        _add_wzpy_path(wzpy_path.resolve())

    try:
        import wzpy  # noqa: F401
        return
    except ImportError:
        pass

    if no_bootstrap:
        raise SystemExit(
            "wzpy is not importable. Install/clone wz-python and pass "
            "--wzpy-path, or run without --no-bootstrap."
        )

    _bootstrap_wzpy(DEFAULT_WZPY_DIR)
    _add_wzpy_path(DEFAULT_WZPY_DIR)

    try:
        import wzpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "wzpy was cloned but cannot be imported. Run "
            "`python -m pip install -r requirements.txt` first."
        ) from exc


def _xml_tag(prop: Any) -> str:
    return {
        "Null": "null",
        "Short": "short",
        "Int": "int",
        "Long": "long",
        "Float": "float",
        "Double": "double",
        "String": "string",
        "Vector": "vector",
        "SubProperty": "imgdir",
        "Canvas": "canvas",
        "Sound": "sound",
        "UOL": "uol",
        "Convex": "extended",
    }.get(prop.type_name, "property")


def _read_canvas_bytes(prop: Any) -> bytes:
    from wzpy.canvas import _read_canvas_bytes as wzpy_read_canvas_bytes

    return wzpy_read_canvas_bytes(prop)


def _read_image_body(image: Any) -> bytes:
    wz_file = image.wz_file
    with wz_file.reader_lock:
        reader = wz_file.reader
        keep = reader.position
        reader.seek(image.offset)
        data = reader.read(image.size)
        reader.seek(keep)
    return data


def _read_sound_bytes(prop: Any) -> bytes:
    if getattr(prop, "_data", None) is not None:
        return prop._data
    if prop._wz_image is None:
        return b""

    reader = prop._wz_image.wz_file.reader
    keep = reader.position
    reader.seek(prop._data_offset)
    data = reader.read(prop._data_length)
    reader.seek(keep)
    return data


def _property_to_xml(
    prop: Any,
    *,
    indent: int,
    context: str,
) -> str:
    from wzpy.properties import (
        WzCanvasProperty,
        WzConvexProperty,
        WzNullProperty,
        WzSoundProperty,
        WzSubProperty,
        WzUolProperty,
        WzVectorProperty,
    )

    pad = "  " * indent
    tag = _xml_tag(prop)
    name_attr = f"name={quoteattr(prop.name)}"

    if isinstance(prop, WzNullProperty):
        return f"{pad}<{tag} {name_attr}/>"

    if isinstance(prop, WzVectorProperty):
        return f'{pad}<{tag} {name_attr} x="{prop.x}" y="{prop.y}"/>'

    if isinstance(prop, WzCanvasProperty):
        raw = _read_canvas_bytes(prop) if prop.has_pixels() else b""
        attrs = [
            name_attr,
            f'width="{prop.width}"',
            f'height="{prop.height}"',
            f'format="{prop.format}"',
            f'format2="{prop.format2}"',
        ]
        if raw:
            attrs.append(f'rawlength="{len(raw)}"')
            attrs.append(f"rawdata={quoteattr(base64.b64encode(raw).decode('ascii'))}")

        if not prop.has_children():
            return f"{pad}<{tag} {' '.join(attrs)}/>"

        body = "\n".join(
            _property_to_xml(
                child,
                indent=indent + 1,
                context=f"{context}/{child.name}",
            )
            for child in prop.children()
        )
        return f"{pad}<{tag} {' '.join(attrs)}>\n{body}\n{pad}</{tag}>"

    if isinstance(prop, WzSoundProperty):
        raw = _read_sound_bytes(prop)
        header = getattr(prop, "header", b"")
        return (
            f'{pad}<{tag} {name_attr} length_ms="{prop.length_ms}" '
            f'value="{prop.value}" header={quoteattr(base64.b64encode(header).decode("ascii"))} '
            f'rawlength="{len(raw)}" rawdata={quoteattr(base64.b64encode(raw).decode("ascii"))}/>'
        )

    if isinstance(prop, WzConvexProperty):
        body = "\n".join(
            f'{pad}  <vector x="{point.x}" y="{point.y}"/>'
            for point in prop.points
        )
        return f"{pad}<{tag} {name_attr}>\n{body}\n{pad}</{tag}>"

    if isinstance(prop, WzUolProperty):
        return f"{pad}<{tag} {name_attr} value={quoteattr(str(prop.value))}/>"

    if isinstance(prop, WzSubProperty):
        if not prop.has_children():
            return f"{pad}<{tag} {name_attr}/>"

        body = "\n".join(
            _property_to_xml(
                child,
                indent=indent + 1,
                context=f"{context}/{child.name}",
            )
            for child in prop.children()
        )
        return f"{pad}<{tag} {name_attr}>\n{body}\n{pad}</{tag}>"

    try:
        value = prop.value
    except Exception:
        value = ""
    return f"{pad}<{tag} {name_attr} value={quoteattr(str(value))}/>"


def image_to_server_xml(
    image: Any,
    *,
    image_context: str,
) -> str:
    raw_body = _read_image_body(image)
    image.parse()
    body = "\n".join(
        _property_to_xml(
            child,
            indent=1,
            context=f"{image_context}/{child.name}",
        )
        for child in image.children()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<imgdir name={quoteattr(image.name)} wz_rawlength=\"{len(raw_body)}\" "
        f"wz_rawbody={quoteattr(base64.b64encode(raw_body).decode('ascii'))}>\n"
        f"{body}\n"
        "</imgdir>\n"
    )


def _iter_wz_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".wz":
            raise SystemExit(f"input file is not a .wz file: {input_path}")
        return [input_path]

    if not input_path.is_dir():
        raise SystemExit(f"input path does not exist: {input_path}")

    return sorted(
        path
        for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() == ".wz"
    )


def _xml_target_path(output_dir: Path, wz_path: Path, rel_img_path: str) -> Path:
    clean_rel = rel_img_path.replace("\\", "/").strip("/")
    img_path = PurePosixPath(clean_rel)
    return output_dir / wz_path.name / Path(*img_path.parts).with_name(
        img_path.name + ".xml"
    )


def _write_wz_meta(wz_file: Any, output_dir: Path, wz_path: Path) -> None:
    target = output_dir / wz_path.name / "_wz_meta.xml"
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        (
            f'<wzmeta name={quoteattr(wz_path.name)} '
            f'fsize="{wz_file.header.fsize}" fstart="{wz_file.header.fstart}" '
            f'copyright={quoteattr(wz_file.header.copyright)}>'
        ),
    ]

    def walk_dir(directory: Any) -> None:
        for sub in directory.subdirs.values():
            entry_kind = getattr(sub, "_entry_kind", 3)
            string_offset = getattr(sub, "_entry_string_offset", None)
            extra = f' entryKind="{entry_kind}"'
            if string_offset is not None:
                extra += f' stringOffset="{string_offset}"'
            lines.append(
                f'  <dir path={quoteattr(sub.path)} size="{getattr(sub, "_entry_size", 0)}" '
                f'checksum="{getattr(sub, "_checksum", 0)}"{extra}/>'
            )
            walk_dir(sub)
        for image in directory.images.values():
            entry_kind = getattr(image, "_entry_kind", 4)
            string_offset = getattr(image, "_entry_string_offset", None)
            extra = f' entryKind="{entry_kind}"'
            if string_offset is not None:
                extra += f' stringOffset="{string_offset}"'
            lines.append(
                f'  <img path={quoteattr(image.path)} size="{image.size}" '
                f'checksum="{getattr(image, "_checksum", 0)}"{extra}/>'
            )

    walk_dir(wz_file.root)
    lines.append("</wzmeta>")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def export_wz_file(
    wz_path: Path,
    output_dir: Path,
    *,
    region: str,
    version: int | None,
    entry: str | None,
    limit: int | None,
    stop_on_error: bool,
    logger: ExportLogger,
) -> tuple[int, int]:
    from wzpy import WzFile

    exported = 0
    failed = 0

    with WzFile.open(str(wz_path), region=region, version=version) as wz_file:
        if entry:
            node = wz_file.root.get(entry)
            if node is None:
                raise SystemExit(f"entry not found in {wz_path.name}: {entry}")
            images = [(entry, node)]
        else:
            images = list(wz_file.root.walk_images())

        if limit is not None:
            images = images[:limit]

        total = len(images)
        print(f"{wz_path.name}: exporting {total} image(s)")

        for index, (rel_path, image) in enumerate(images, 1):
            target = _xml_target_path(output_dir, wz_path, rel_path)
            try:
                context = f"{wz_path.name}/{rel_path}"
                xml_text = image_to_server_xml(
                    image,
                    image_context=context,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(xml_text, encoding="utf-8", newline="\n")
                exported += 1
                print(f"  [{index:>5}/{total}] {rel_path} -> {target}")
            except Exception as exc:  # noqa: BLE001 - batch exporter should report.
                failed += 1
                print(f"  [{index:>5}/{total}] {rel_path}: ERROR {exc}", file=sys.stderr)
                logger.error(f"{wz_path.name} [{index}/{total}] {rel_path}: {exc}")
                if stop_on_error:
                    raise

        _write_wz_meta(wz_file, output_dir, wz_path)

    return exported, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export MapleStory GMS v83 .wz files to XML while preserving WZ paths."
    )
    parser.add_argument("input", help="A .wz file or a directory containing .wz files.")
    parser.add_argument("output", help="Directory where XML files will be written.")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument(
        "--version",
        type=int,
        default=83,
        help="WZ version. Default: 83 for old GMS v083.",
    )
    parser.add_argument(
        "--auto-version",
        action="store_true",
        help="Let wzpy auto-detect WZ version instead of forcing 83.",
    )
    parser.add_argument("--entry", help="Export one .img path inside each .wz.")
    parser.add_argument("--limit", type=int, help="Export only the first N .img files.")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop when one .img fails instead of continuing.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Directory for timestamped logs. Default: <output>/_logs.",
    )
    parser.add_argument(
        "--wzpy-path",
        type=Path,
        help="Path to a local clone of https://github.com/Leonana69/wz-python.",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Do not auto-clone wz-python when wzpy is missing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _load_wzpy(args.wzpy_path, args.no_bootstrap)

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    logs_dir = (args.logs_dir or (output_dir / "_logs")).expanduser().resolve()
    version = None if args.auto_version else args.version
    wz_files = _iter_wz_files(input_path)

    if not wz_files:
        print(f"No .wz files found under {input_path}")
        return 0

    total_exported = 0
    total_failed = 0
    logger = ExportLogger(logs_dir)
    try:
        print(f"Input: {input_path}")
        print(f"Output: {output_dir}")
        print(f"Region: {args.region}, version: {version}")
        print(f"WZ files: {len(wz_files)}")

        for wz_path in wz_files:
            exported, failed = export_wz_file(
                wz_path,
                output_dir,
                region=args.region,
                version=version,
                entry=args.entry,
                limit=args.limit,
                stop_on_error=args.stop_on_error,
                logger=logger,
            )
            total_exported += exported
            total_failed += failed

        if logger.warning_count or logger.error_count or total_failed:
            logger.write(
                "SUMMARY",
                f"exported={total_exported} failed={total_failed} "
                f"warnings={logger.warning_count} errors={logger.error_count}",
            )
    finally:
        logger.close()

    print(f"Done. Exported: {total_exported}, failed: {total_failed}")
    print(f"Log: {logger.path}")
    print(f"Latest log: {logger.latest_path}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
