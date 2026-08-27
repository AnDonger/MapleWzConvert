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
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any
from xml.sax.saxutils import quoteattr
import zlib


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


def _decode_legacy_chunked_canvas(prop: Any):
    from PIL import Image
    from wzpy.canvas import _decode_pixels

    raw = _read_canvas_bytes(prop)
    fmt = prop.format + prop.format2
    attempts: list[tuple[str, bytes, bool]] = []

    def add_attempt(name: str, payload: bytes, infer_geometry: bool = False) -> None:
        if payload:
            attempts.append((name, payload, infer_geometry))
            if len(payload) > 1 and payload[0] == 0:
                attempts.append((name + "-skip-leading-zero", payload[1:], infer_geometry))

    def zlib_lenient(data: bytes, wbits: int = zlib.MAX_WBITS) -> bytes:
        decompressor = zlib.decompressobj(wbits)
        return decompressor.decompress(data) + decompressor.flush()

    def dechunk(start: int) -> bytes:
        pos = start
        chunks = bytearray()
        while pos + 4 <= len(raw):
            chunk_len = int.from_bytes(raw[pos:pos + 4], "little")
            pos += 4
            if chunk_len <= 0 or pos + chunk_len > len(raw):
                raise ValueError(
                    f"bad legacy chunk length {chunk_len} at offset {pos - 4}"
                )
            chunks += raw[pos:pos + chunk_len]
            pos += chunk_len
        if any(raw[pos:]):
            raise ValueError(f"non-zero trailing bytes after offset {pos}")
        return bytes(chunks)

    add_attempt("raw", raw)
    if len(raw) > 2:
        add_attempt("raw-skip2", raw[2:])

    for start in (0, 2):
        try:
            add_attempt(f"legacy-chunks-start{start}", dechunk(start))
        except ValueError:
            pass

    for offset in range(min(16, len(raw))):
        add_attempt(f"offset{offset}", raw[offset:], infer_geometry=True)

    def inferred_image(pixels: bytes) -> Image.Image:
        candidate_formats = []
        if fmt in (1, 2, 257, 513, 517):
            candidate_formats.append(fmt)
        candidate_formats.extend(candidate for candidate in (1, 2, 257, 513) if candidate not in candidate_formats)

        errors: list[str] = []
        for candidate_fmt in candidate_formats:
            bytes_per_pixel = 4 if candidate_fmt == 2 else 2
            if len(pixels) % bytes_per_pixel:
                continue

            pixel_count = len(pixels) // bytes_per_pixel
            if pixel_count <= 0:
                continue

            dimensions: set[tuple[int, int]] = set()
            width = prop.width if isinstance(prop.width, int) else 0
            height = prop.height if isinstance(prop.height, int) else 0

            if width > 0 and height > 0 and width * height == pixel_count:
                dimensions.add((width, height))
            if width > 0 and pixel_count % width == 0:
                dimensions.add((width, pixel_count // width))
            if height > 0 and pixel_count % height == 0:
                dimensions.add((pixel_count // height, height))

            for candidate_width in range(1, int(pixel_count**0.5) + 1):
                if pixel_count % candidate_width == 0:
                    candidate_height = pixel_count // candidate_width
                    dimensions.add((candidate_width, candidate_height))
                    dimensions.add((candidate_height, candidate_width))

            if width > 0 and height > 0:
                key = lambda item: abs(item[0] - width) + abs(item[1] - height)
            else:
                key = lambda item: abs(item[0] - item[1])

            for image_width, image_height in sorted(dimensions, key=key):
                if image_width <= 0 or image_height <= 0:
                    continue
                if image_width > 4096 or image_height > 4096:
                    continue
                try:
                    return _decode_pixels(pixels, image_width, image_height, candidate_fmt)
                except Exception as exc:  # noqa: BLE001 - try inferred candidates.
                    errors.append(
                        f"fmt={candidate_fmt} size={image_width}x{image_height}: {exc}"
                    )

        raise ValueError("could not infer canvas geometry: " + " | ".join(errors[:20]))

    errors: list[str] = []
    for name, payload, infer_geometry in attempts:
        for zlib_name, zlib_payload in (
            ("zlib", payload),
            ("zlib-skip2", payload[2:] if len(payload) > 2 else b""),
            ("raw-deflate", payload),
        ):
            if not zlib_payload:
                continue
            try:
                wbits = -zlib.MAX_WBITS if zlib_name == "raw-deflate" else zlib.MAX_WBITS
                pixels = zlib_lenient(zlib_payload, wbits=wbits)
                try:
                    return _decode_pixels(pixels, prop.width, prop.height, fmt)
                except Exception:
                    if infer_geometry:
                        return inferred_image(pixels)
                    raise
            except Exception as exc:  # noqa: BLE001 - try all legacy variants.
                errors.append(f"{name}/{zlib_name}: {exc}")

    raise ValueError("legacy v83 canvas fallback failed: " + " | ".join(errors))


def _canvas_rawdata(prop: Any) -> str:
    if not prop.has_pixels():
        return ""
    return base64.b64encode(_read_canvas_bytes(prop)).decode("ascii")


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


def _canvas_png(prop: Any, region: str) -> tuple[str, int, int]:
    from wzpy.canvas import decode_canvas

    if not prop.has_pixels():
        return "", max(0, prop.width), max(0, prop.height)

    try:
        image = decode_canvas(prop, region=region)
    except Exception:
        image = _decode_legacy_chunked_canvas(prop)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii"), image.width, image.height


def _property_to_xml(
    prop: Any,
    *,
    indent: int,
    region: str,
    canvas_data: str,
    include_canvas_format: bool,
    logger: ExportLogger | None,
    context: str,
    strict_canvas_errors: bool,
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
        canvas_width = prop.width
        canvas_height = prop.height
        basedata = None
        rawdata = None

        if canvas_data in {"raw", "both"}:
            rawdata = _canvas_rawdata(prop)

        if canvas_data in {"png", "both"}:
            try:
                basedata, canvas_width, canvas_height = _canvas_png(prop, region)
            except Exception as exc:  # noqa: BLE001 - keep the .img exportable.
                if logger is not None:
                    logger.warning(f"{context}: canvas basedata export failed: {exc}")
                if strict_canvas_errors:
                    raise
                basedata = ""

        attrs = [
            name_attr,
            f'width="{canvas_width}"',
            f'height="{canvas_height}"',
        ]
        if canvas_data in {"raw", "both"}:
            attrs.append(f'format="{prop.format}"')
            attrs.append(f'format2="{prop.format2}"')
        elif include_canvas_format:
            attrs.append(f'format="{prop.format}"')
            attrs.append(f'format2="{prop.format2}"')
        if basedata is not None:
            attrs.append(f"basedata={quoteattr(basedata)}")
        if rawdata is not None:
            attrs.append(f'rawlength="{len(_read_canvas_bytes(prop))}"')
            attrs.append(f"rawdata={quoteattr(rawdata)}")

        if not prop.has_children():
            return f"{pad}<{tag} {' '.join(attrs)}/>"

        body = "\n".join(
            _property_to_xml(
                child,
                indent=indent + 1,
                region=region,
                canvas_data=canvas_data,
                include_canvas_format=include_canvas_format,
                logger=logger,
                context=f"{context}/{child.name}",
                strict_canvas_errors=strict_canvas_errors,
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
                region=region,
                canvas_data=canvas_data,
                include_canvas_format=include_canvas_format,
                logger=logger,
                context=f"{context}/{child.name}",
                strict_canvas_errors=strict_canvas_errors,
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
    region: str,
    canvas_data: str,
    include_canvas_format: bool,
    logger: ExportLogger | None,
    image_context: str,
    strict_canvas_errors: bool,
) -> str:
    raw_body = _read_image_body(image)
    image.parse()
    body = "\n".join(
        _property_to_xml(
            child,
            indent=1,
            region=region,
            canvas_data=canvas_data,
            include_canvas_format=include_canvas_format,
            logger=logger,
            context=f"{image_context}/{child.name}",
            strict_canvas_errors=strict_canvas_errors,
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
    canvas_data: str,
    include_canvas_format: bool,
    stop_on_error: bool,
    strict_canvas_errors: bool,
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
                    region=region,
                    canvas_data=canvas_data,
                    include_canvas_format=include_canvas_format,
                    logger=logger,
                    image_context=context,
                    strict_canvas_errors=strict_canvas_errors,
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
        "--canvas-data",
        choices=("raw", "png", "both", "none"),
        default="raw",
        help=(
            "Canvas export mode. raw preserves the original WZ canvas payload "
            "as rawdata; png writes decoded PNG basedata; both writes both. "
            "Default: raw."
        ),
    )
    parser.add_argument(
        "--skip-canvas-data",
        action="store_true",
        help="Deprecated alias for --canvas-data none.",
    )
    parser.add_argument(
        "--include-canvas-format",
        action="store_true",
        help="Also write raw canvas format and format2 attributes.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop when one .img fails instead of continuing.",
    )
    parser.add_argument(
        "--strict-canvas-errors",
        action="store_true",
        help="Treat one failed canvas basedata export as a failed .img.",
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
    canvas_data = "none" if args.skip_canvas_data else args.canvas_data
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
        print(f"Canvas data: {canvas_data}")
        print(f"WZ files: {len(wz_files)}")

        for wz_path in wz_files:
            exported, failed = export_wz_file(
                wz_path,
                output_dir,
                region=args.region,
                version=version,
                entry=args.entry,
                limit=args.limit,
                canvas_data=canvas_data,
                include_canvas_format=args.include_canvas_format,
                stop_on_error=args.stop_on_error,
                strict_canvas_errors=args.strict_canvas_errors,
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
