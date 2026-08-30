"""Export legacy MapleStory .wz files to raw .img files.

Each .img body is copied byte-for-byte from the WZ container.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Any


class ExportLogger:
    """Write only warning/error information to logs."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"wz_img_export_{stamp}.log"
        self.latest_path = logs_dir / "wz_img_export_latest.log"
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


def _load_wzpy(wzpy_path: Path | None, no_bootstrap: bool) -> None:
    if wzpy_path is not None or no_bootstrap:
        print("Note: --wzpy-path/--no-bootstrap are ignored; using project-local maplewz_sdk.")
    from maplewz_sdk import ensure_wzpy_importable

    ensure_wzpy_importable()


def _iter_wz_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.wz") if p.is_file())


def _read_image_body(wz_file: Any, image: Any) -> bytes:
    reader = wz_file.reader
    with wz_file.reader_lock:
        keep = reader.position
        reader.seek(image.offset)
        data = reader.read(image.size)
        reader.seek(keep)
    return data


def export_wz_to_img(
    wz_path: Path,
    output_dir: Path,
    *,
    region: str,
    version: int | None,
    logger: ExportLogger,
) -> tuple[int, int]:
    from wzpy import WzFile

    target_root = output_dir / wz_path.name
    target_root.mkdir(parents=True, exist_ok=True)

    exported = 0
    failed = 0
    with WzFile.open(str(wz_path), region=region, version=version) as wz:
        images = list(wz.root.walk_images())
        print(f"{wz_path.name}: exporting {len(images)} image(s)")
        for index, (image_path, image) in enumerate(images, 1):
            target = target_root / Path(image_path)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_read_image_body(wz, image))
                exported += 1
                print(f"  [{index:>5}/{len(images)}] {image_path} -> {target}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.error(f"{wz_path}:{image_path}: {exc}")
                print(f"  [{index:>5}/{len(images)}] {image_path}: ERROR")

    return exported, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export .wz files to raw .img files.")
    parser.add_argument("input", help="Input .wz file or directory containing .wz files.")
    parser.add_argument("output", help="Output directory.")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
    parser.add_argument("--version", type=int, default=83, help="WZ version. Default: 83.")
    parser.add_argument(
        "--auto-version",
        action="store_true",
        help="Detect WZ version instead of forcing --version.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Directory for timestamped logs. Default: <output>/_logs.",
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
    logs_dir = (args.logs_dir or (output_dir / "_logs")).expanduser().resolve()
    version = None if args.auto_version else args.version
    wz_files = _iter_wz_files(input_path)

    if not wz_files:
        raise SystemExit(f"no .wz files found: {input_path}")

    print(f"Input: {input_path}")
    print(f"Output: {output_dir}")
    print(f"Region: {args.region}, version: {'auto' if version is None else version}")
    print(f"WZ files: {len(wz_files)}")

    logger = ExportLogger(logs_dir)
    total_exported = 0
    total_failed = 0
    try:
        for wz_path in wz_files:
            exported, failed = export_wz_to_img(
                wz_path,
                output_dir,
                region=args.region,
                version=version,
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
