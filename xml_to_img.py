"""Pack MapleWzConvert .img.xml files back to raw .img files."""

from __future__ import annotations

import argparse
from datetime import datetime
import io
from pathlib import Path, PurePosixPath
import sys
import xml.etree.ElementTree as ET

from xml_to_wz import SyntheticWzFile, _load_wzpy, _parse_property


class PackLogger:
    """Write only warning/error information to logs."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"xml_img_pack_{stamp}.log"
        self.latest_path = logs_dir / "xml_img_pack_latest.log"
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


def _iter_wz_dirs(input_path: Path) -> list[Path]:
    if not input_path.is_dir():
        raise SystemExit(f"input is not a directory: {input_path}")
    if input_path.name.lower().endswith(".wz"):
        return [input_path]
    return sorted(
        path
        for path in input_path.iterdir()
        if path.is_dir() and path.name.lower().endswith(".wz")
    )


def _find_xml_files(wz_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in wz_dir.rglob("*.xml")
        if path.is_file() and "_logs" not in path.parts and path.name != "_wz_meta.xml"
    )


def _img_name_from_xml_path(xml_path: Path) -> str:
    name = xml_path.name
    if name.lower().endswith(".xml"):
        return name[:-4]
    return name


def _img_target_path(output_dir: Path, wz_dir: Path, xml_path: Path) -> Path:
    rel = xml_path.relative_to(wz_dir)
    parts = list(rel.parts)
    parts[-1] = _img_name_from_xml_path(xml_path)
    return output_dir / wz_dir.name / Path(*parts)


def _xml_path_text(wz_dir: Path, xml_path: Path) -> str:
    return PurePosixPath(*xml_path.relative_to(wz_dir).parts).as_posix()


def _build_raw_img_from_xml(
    xml_path: Path,
    *,
    image_name: str,
    region: str,
    version: int,
    logger: PackLogger,
) -> tuple[bytes, str]:
    from wzpy.crypto import WzKey, compute_version_hash
    from wzpy.reader import WzBinaryReader
    from wzpy.writer import encode_image_body
    from wzpy.wz_image import WzImage

    raw_file_bytes = xml_path.read_bytes()
    reader = WzBinaryReader(io.BytesIO(b""), WzKey.for_region(region), header_fstart=0)
    reader.version_hash = compute_version_hash(version)
    holder = SyntheticWzFile(reader)
    image = WzImage(image_name, parent=None, offset=0, size=0, wz_file=holder)

    try:
        root_element = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return raw_file_bytes, "raw-file"

    if root_element.tag.lower() != "imgdir":
        return raw_file_bytes, "raw-file"

    image._root = _parse_property(
        root_element,
        None,
        image=image,
        region=region,
        logger=logger,
        context=image_name,
    )
    image._root.name = image_name
    image._parsed = True
    return encode_image_body(image, reader), "rebuilt"


def pack_wz_xml_dir_to_img(
    wz_dir: Path,
    output_dir: Path,
    *,
    region: str,
    version: int,
    logger: PackLogger,
) -> tuple[int, int]:
    xml_files = _find_xml_files(wz_dir)
    print(f"{wz_dir.name}: packing {len(xml_files)} image(s)")

    packed = 0
    failed = 0
    for index, xml_path in enumerate(xml_files, 1):
        rel_text = _xml_path_text(wz_dir, xml_path)
        target = _img_target_path(output_dir, wz_dir, xml_path)
        image_name = target.name
        try:
            raw_img, mode = _build_raw_img_from_xml(
                xml_path,
                image_name=image_name,
                region=region,
                version=version,
                logger=logger,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw_img)
            packed += 1
            print(f"  [{index:>5}/{len(xml_files)}] {rel_text} -> {target} [{mode}]")
        except Exception as exc:  # noqa: BLE001 - keep going for batch conversion.
            failed += 1
            logger.error(f"{wz_dir.name}/{rel_text}: {exc}")
            print(f"  [{index:>5}/{len(xml_files)}] {rel_text}: ERROR {exc}", file=sys.stderr)

    return packed, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack .img.xml folders, such as Map.wz/, back to raw .img files."
    )
    parser.add_argument("input", help="A .wz XML directory or a directory containing .wz directories.")
    parser.add_argument("output", help="Directory where raw .img files will be written.")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument("--version", type=int, default=83, help="WZ version. Default: 83.")
    parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Directory for warning/error logs. Default: <output>/_logs.",
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

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    wz_dirs = _iter_wz_dirs(input_path)
    if not wz_dirs:
        raise SystemExit(f"no .wz directories found: {input_path}")

    logs_dir = (args.logs_dir or (output_dir / "_logs")).expanduser().resolve()
    logger = PackLogger(logs_dir)
    total_packed = 0
    total_failed = 0
    try:
        print(f"Input: {input_path}")
        print(f"Output: {output_dir}")
        print(f"Region: {args.region}, version: {args.version}")
        print(f"WZ dirs: {len(wz_dirs)}")
        for wz_dir in wz_dirs:
            packed, failed = pack_wz_xml_dir_to_img(
                wz_dir,
                output_dir,
                region=args.region,
                version=args.version,
                logger=logger,
            )
            total_packed += packed
            total_failed += failed
        if logger.warning_count or logger.error_count or total_failed:
            logger.write(
                "SUMMARY",
                f"packed={total_packed} failed={total_failed} "
                f"warnings={logger.warning_count} errors={logger.error_count}",
            )
    finally:
        logger.close()

    print(f"Done. Packed: {total_packed}, failed: {total_failed}")
    print(f"Log: {logger.path}")
    print(f"Latest log: {logger.latest_path}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
