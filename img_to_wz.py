"""Pack raw .img files back into a legacy MapleStory .wz file.

The input is usually a folder named like ``Map.wz`` created by
``wz_to_img.py``. Every ``.img`` file is read as raw bytes and written as the
corresponding WZ image body. The script does not parse, validate, normalize, or
repair the ``.img`` content.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import io
from pathlib import Path, PurePosixPath
import sys
from typing import Any

from xml_to_wz import (
    DEFAULT_COPYRIGHT,
    SyntheticWzFile,
    _ensure_directory,
    _load_wzpy,
    _set_raw_image_body,
)


class PackLogger:
    """Write only warning/error information to logs."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"wz_img_pack_{stamp}.log"
        self.latest_path = logs_dir / "wz_img_pack_latest.log"
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


def _find_img_files(root_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in root_dir.rglob("*.img")
        if path.is_file() and "_logs" not in path.parts
    )


def _image_path_from_img(root_dir: Path, img_path: Path) -> PurePosixPath:
    rel = img_path.relative_to(root_dir)
    return PurePosixPath(*rel.parts)


def build_wz_from_img(
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

    img_files = _find_img_files(input_dir)
    if not img_files:
        raise SystemExit(f"no .img files found under {input_dir}")

    print(f"IMG files: {len(img_files)}")
    for index, img_path in enumerate(img_files, 1):
        image_path = _image_path_from_img(input_dir, img_path)
        parent = _ensure_directory(wz.root, image_path.parts[:-1])
        image_name = image_path.name

        try:
            raw_body = img_path.read_bytes()
            image = WzImage(image_name, parent=parent, offset=0, size=0, wz_file=holder)
            _set_raw_image_body(image, raw_body)
            parent.images[image_name] = image
            print(f"  [{index:>5}/{len(img_files)}] {image_path}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{img_path}: {exc}")
            raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return wz.save_as(str(output_path))


def default_output_path(input_dir: Path) -> Path:
    if input_dir.name.lower().endswith(".wz"):
        return input_dir.with_suffix(".packed.wz")
    return input_dir.with_suffix(".wz")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack a raw .img folder, such as Map.wz/, back into a .wz file."
    )
    parser.add_argument("input_dir", help="Folder containing raw .img files, often named Map.wz.")
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
        print(f"Input: {input_dir}")
        print(f"Output: {output_path}")
        print(f"Region: {args.region}, version: {args.version}")
        print(f"fstart: {fstart}")
        try:
            data_size = build_wz_from_img(
                input_dir,
                output_path,
                region=args.region,
                version=args.version,
                copyright=copyright,
                fstart=fstart,
                logger=logger,
            )
        except Exception as exc:  # noqa: BLE001
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
