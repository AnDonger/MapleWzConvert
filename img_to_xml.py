"""Export raw .img files to MapleWzConvert XML files."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path, PurePosixPath
import sys

from wz_to_xml import _load_wzpy, image_to_server_xml


class ExportLogger:
    """Write only warning/error information to logs."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"img_xml_export_{stamp}.log"
        self.latest_path = logs_dir / "img_xml_export_latest.log"
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


def _find_img_files(wz_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in wz_dir.rglob("*.img")
        if path.is_file() and "_logs" not in path.parts
    )


def _xml_target_path(output_dir: Path, wz_dir: Path, img_path: Path) -> Path:
    rel = img_path.relative_to(wz_dir)
    posix_rel = PurePosixPath(*rel.parts)
    return output_dir / wz_dir.name / Path(*posix_rel.parts).with_name(
        posix_rel.name + ".xml"
    )


def export_wz_img_dir_to_xml(
    wz_dir: Path,
    output_dir: Path,
    *,
    region: str,
    logger: ExportLogger,
) -> tuple[int, int]:
    from wzpy.crypto import WzKey
    from wzpy.wz_image import WzImage

    img_files = _find_img_files(wz_dir)
    print(f"{wz_dir.name}: exporting {len(img_files)} image(s)")

    exported = 0
    failed = 0
    key = WzKey.for_region(region)
    for index, img_path in enumerate(img_files, 1):
        rel_path = img_path.relative_to(wz_dir)
        rel_text = PurePosixPath(*rel_path.parts).as_posix()
        target = _xml_target_path(output_dir, wz_dir, img_path)
        try:
            image = WzImage.from_bytes(img_path.read_bytes(), key=key, name=img_path.name)
            xml_text = image_to_server_xml(
                image,
                image_context=f"{wz_dir.name}/{rel_text}",
            )
            for warning in getattr(image, "parse_warnings", []) or []:
                logger.warning(f"{wz_dir.name}/{rel_text}: {warning}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(xml_text, encoding="utf-8", newline="\n")
            exported += 1
            print(f"  [{index:>5}/{len(img_files)}] {rel_text} -> {target}")
        except Exception as exc:  # noqa: BLE001 - batch exporter should report.
            failed += 1
            logger.error(f"{wz_dir.name}/{rel_text}: {exc}")
            print(f"  [{index:>5}/{len(img_files)}] {rel_text}: ERROR {exc}", file=sys.stderr)

    return exported, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export raw .img folders, such as Map.wz/, to .img.xml files."
    )
    parser.add_argument("input", help="A .wz directory or a directory containing .wz directories.")
    parser.add_argument("output", help="Directory where .img.xml files will be written.")
    parser.add_argument("--region", default="GMS", help="WZ region cipher. Default: GMS.")
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
    logger = ExportLogger(logs_dir)
    total_exported = 0
    total_failed = 0
    try:
        print(f"Input: {input_path}")
        print(f"Output: {output_dir}")
        print(f"Region: {args.region}")
        print(f"WZ dirs: {len(wz_dirs)}")
        for wz_dir in wz_dirs:
            exported, failed = export_wz_img_dir_to_xml(
                wz_dir,
                output_dir,
                region=args.region,
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
