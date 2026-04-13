#!/usr/bin/env python3
"""
DicomPressor Vatech - dedicated merge workflow for Vatech 3D studies.

Handles two input patterns in parallel:
  1. Normal folders with many single-frame DICOM slices
  2. Vatech archive files like DCM_FILE.CT (ZIP archives with DICOM slices)

The original dicompressor.py remains unchanged for the generic workflow.
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

try:
    import pydicom
except ImportError:
    print("ERROR: pydicom is required. Install with: pip install pydicom")
    sys.exit(1)

# Add current directory to path for local imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dicom_utils import find_dicom_files, is_dicom_file, merge_files_to_multiframe

VERSION = "1.0.0-vatech"
PROGRAM_NAME = "DicomPressor Vatech"
DONE_MARKER = ".dicompressor_vatech_done"
ARCHIVE_SUFFIXES = (".ct", ".ct.dcm")
# Ignore tiny direct DICOM series (for example PX/DX images in patient root folders).
# Real 3D CT/CBCT folders typically contain dozens or hundreds of slices.
MIN_DIRECT_SERIES_FILES = 8
DEFAULT_LOG_FILENAME = "dicompressor-vatech.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
SCAN_PROGRESS_EVERY_FOLDERS = 250
SCAN_PROGRESS_EVERY_SECONDS = 15.0

logger = logging.getLogger("dicompressor.vatech")


def default_log_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_LOG_FILENAME)


def supports_unicode_output(stream=None) -> bool:
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "╔═║╝".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def configure_logging(verbose: bool, quiet: bool, log_file: str = "") -> str:
    console_level = logging.INFO
    if verbose:
        console_level = logging.DEBUG
    elif quiet:
        console_level = logging.WARNING

    effective_log_file = os.path.abspath(log_file) if log_file else default_log_path()
    log_dir = os.path.dirname(effective_log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.DEBUG)

    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = RotatingFileHandler(
        effective_log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)
    logger.debug(
        "Logging configured: console=%s file=%s",
        logging.getLevelName(console_level),
        effective_log_file,
    )
    return effective_log_file


def print_banner() -> None:
    if supports_unicode_output():
        print(
            f"""
╔══════════════════════════════════════════════════════╗
║  {PROGRAM_NAME} v{VERSION:<28}║
║  Vatech 3D Merge Workflow                          ║
║  Handles folders of slices and DCM_FILE.CT archives║
╚══════════════════════════════════════════════════════╝
"""
        )
        return

    print(
        f"""
+------------------------------------------------------+
|  {PROGRAM_NAME} v{VERSION:<28}|
|  Vatech 3D Merge Workflow                            |
|  Handles folders of slices and DCM_FILE.CT archives  |
+------------------------------------------------------+
"""
    )


def print_help_detailed() -> None:
    print_banner()
    rule = "======" if not supports_unicode_output() else "══════"
    rule_short = "==========" if not supports_unicode_output() else "═══════════"
    rule_options = "========" if not supports_unicode_output() else "════════"
    rule_long = "==============" if not supports_unicode_output() else "══════════════"
    rule_examples = "========" if not supports_unicode_output() else "═════════"
    print(
        f"""
USAGE:
{rule}

  python dicompressor-vatech.py -j [options] -f/-F <folder>

PATH MODES:
{rule_short}

  -f PATH       Scan PATH recursively and process every folder that contains
                either single-frame DICOM slices or Vatech .CT archives.

  -F PATH       Process PATH only (no subfolders).

OPTIONS:
{rule_options}

  -h                Display this help text
  -c                Display version information
  -j                Merge workflow (required)
  --skip-if-done    Skip folders that already contain {DONE_MARKER}
  --watch N         Watch mode: re-scan every N seconds and process only
                    new folders. Implies --skip-if-done.
  --output-dir DIR  Copy merged outputs to DIR after successful processing.
  --log-file FILE   Write a rotating log file. Default:
                    {default_log_path()}
  --verbose         Debug-level logging
  --quiet           Warning/error logging only

WHAT THIS SCRIPT DOES:
{rule_long}

  1. Finds folders that contain normal single-frame DICOM slices
  2. Finds Vatech archive files like DCM_FILE.CT or DCM_FILE.CT.dcm
  3. Extracts every archive into a temporary folder
  4. Merges extracted slices into one multi-frame DICOM
  5. Moves the merged result back next to the source data
  6. Deletes the temporary folder
  7. Writes {DONE_MARKER} so the folder is not processed again

EXAMPLES:
{rule_examples}

  # Process one folder with normal DICOM slices or .CT archives:
  python dicompressor-vatech.py -j -F /path/to/patient_folder

  # Recursively scan a parent folder with many patient subfolders:
  python dicompressor-vatech.py -j --skip-if-done -f /path/to/patients

  # Watch mode:
  python dicompressor-vatech.py -j --watch 300 -f /path/to/patients

  # Watch + central output directory:
  python dicompressor-vatech.py -j --watch 300 --output-dir /data/merged -f /data/patients

  # Custom log file:
  python dicompressor-vatech.py -j --watch 300 --log-file /data/logs/vatech.log -f /data/patients
"""
    )


def marker_path(folder: str) -> str:
    return os.path.join(folder, DONE_MARKER)


def is_already_done(folder: str) -> bool:
    return os.path.isfile(marker_path(folder))


def mark_as_done(folder: str, report: Dict[str, object]) -> None:
    info = {
        "processed_at": datetime.now().isoformat(),
        "action": "merge_vatech",
        "variant": "vatech",
        "results": [os.path.basename(p) for p in report["results"]],
        "processed_archives": report["archive_names"],
        "processed_direct_dicom_files": report["direct_dicom_count"],
        "dicompressor_version": VERSION,
    }
    with open(marker_path(folder), "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=2)
    logger.info("Marked as done: %s", marker_path(folder))


def copy_to_output_dir(result_files: Sequence[str], output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    for src in result_files:
        if not os.path.isfile(src):
            continue
        dst = os.path.join(output_dir, os.path.basename(src))
        if os.path.abspath(src) == os.path.abspath(dst):
            continue
        if os.path.exists(dst):
            stem, ext = os.path.splitext(os.path.basename(src))
            counter = 1
            while True:
                candidate = os.path.join(output_dir, f"{stem}_{counter}{ext}")
                if not os.path.exists(candidate):
                    dst = candidate
                    break
                counter += 1
        shutil.copy2(src, dst)
        size_mb = os.path.getsize(dst) / 1024 / 1024
        logger.info("Copied to output: %s (%.1f MB)", dst, size_mb)


def parse_num_frames(dataset) -> int:
    num_frames = getattr(dataset, "NumberOfFrames", 1)
    if isinstance(num_frames, str):
        try:
            num_frames = int(num_frames)
        except ValueError:
            num_frames = 1
    return int(num_frames)


def is_single_frame_dicom(filepath: str) -> bool:
    try:
        dataset = pydicom.dcmread(filepath, stop_before_pixels=True)
    except Exception:
        return False
    return parse_num_frames(dataset) <= 1


def is_vatech_archive_name(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def find_vatech_archives(folder: str) -> List[str]:
    results = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and is_vatech_archive_name(name):
            results.append(path)
    return results


def scan_folder_inputs(folder: str) -> Tuple[List[str], List[str]]:
    return find_mergeable_dicom_files(folder), find_vatech_archives(folder)


def find_mergeable_dicom_files(folder: str) -> List[str]:
    series_groups: Dict[str, List[str]] = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if is_vatech_archive_name(name):
            continue
        if not is_dicom_file(path):
            continue
        try:
            dataset = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:
            continue
        if parse_num_frames(dataset) > 1:
            continue
        series_uid = str(getattr(dataset, "SeriesInstanceUID", "")) or f"unknown:{name}"
        series_groups.setdefault(series_uid, []).append(path)

    results: List[str] = []
    for series_uid, files in sorted(series_groups.items()):
        if len(files) >= MIN_DIRECT_SERIES_FILES:
            results.extend(sorted(files))
        else:
            logger.debug(
                "Ignoring direct series %s in %s with only %d file(s)",
                series_uid,
                folder,
                len(files),
            )
    return results


def iter_scan_roots(root: str, recursive: bool, prune_done: bool = False) -> Iterator[Tuple[int, str]]:
    root = os.path.abspath(root)

    if not recursive:
        yield 1, root
        return

    logger.info("Starting recursive scan under %s", root)
    scanned_count = 0
    scan_started = time.time()
    last_progress_time = scan_started

    for current_root, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if prune_done and is_already_done(current_root):
            logger.debug("Pruning already processed subtree: %s", current_root)
            dirnames[:] = []
        scanned_count += 1
        yield scanned_count, current_root

        now = time.time()
        if (
            scanned_count % SCAN_PROGRESS_EVERY_FOLDERS == 0
            or now - last_progress_time >= SCAN_PROGRESS_EVERY_SECONDS
        ):
            logger.info(
                "Scan progress: scanned %d folder(s). Current=%s",
                scanned_count,
                current_root,
            )
            last_progress_time = now

    logger.info(
        "Finished recursive scan under %s: scanned %d folder(s) in %.1fs",
        root,
        scanned_count,
        time.time() - scan_started,
    )


def safe_extract_zip(archive_path: str, temp_dir: str) -> List[str]:
    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f"{os.path.basename(archive_path)} is not a valid ZIP archive")

    extracted_files: List[str] = []
    temp_root = Path(temp_dir).resolve()

    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if not members:
            raise ValueError(f"{os.path.basename(archive_path)} is empty")

        for info in members:
            destination = temp_root / info.filename
            resolved_destination = destination.resolve()
            try:
                resolved_destination.relative_to(temp_root)
            except ValueError as exc:
                raise ValueError(
                    f"{os.path.basename(archive_path)} contains an unsafe path: {info.filename}"
                ) from exc

            resolved_destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, open(resolved_destination, "wb") as target:
                shutil.copyfileobj(source, target)
            extracted_files.append(str(resolved_destination))

    return extracted_files


def move_output_into_folder(src_path: str, destination_folder: str, created_names: set) -> str:
    destination_folder = os.path.abspath(destination_folder)
    base_name = os.path.basename(src_path)
    stem, ext = os.path.splitext(base_name)
    final_name = base_name
    counter = 1

    while final_name in created_names:
        final_name = f"{stem}_{counter}{ext}"
        counter += 1

    destination = os.path.join(destination_folder, final_name)
    shutil.move(src_path, destination)
    created_names.add(final_name)
    return destination


def merge_direct_dicom_files(dicom_files: Sequence[str], output_folder: str) -> List[str]:
    results = merge_files_to_multiframe(list(dicom_files), output_folder, raise_on_error=True)
    if not results:
        raise ValueError("Single-frame DICOM slices were found, but no merged file was created")
    return results


def process_vatech_archive(archive_path: str, source_folder: str, created_names: set) -> List[str]:
    logger.info("Found Vatech 3D archive: %s", archive_path)
    temp_dir = tempfile.mkdtemp(prefix="dicompressor-vatech-")
    logger.info("Extracting %s into %s", archive_path, temp_dir)

    try:
        safe_extract_zip(archive_path, temp_dir)
        dicom_files = find_dicom_files(temp_dir, include_subfolders=True)
        if not dicom_files:
            raise ValueError(
                f"{os.path.basename(archive_path)} did not contain readable DICOM slices"
            )

        temp_output_dir = os.path.join(temp_dir, "_merged")
        os.makedirs(temp_output_dir, exist_ok=True)
        merged_files = merge_files_to_multiframe(
            dicom_files, temp_output_dir, raise_on_error=True
        )
        if not merged_files:
            raise ValueError(f"{os.path.basename(archive_path)} did not produce a merged output")

        final_outputs = [
            move_output_into_folder(merged_file, source_folder, created_names)
            for merged_file in merged_files
        ]
        return final_outputs
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Removed temp directory: %s", temp_dir)


def process_folder(
    folder: str,
    output_dir: str = "",
    direct_dicom_files: Optional[Sequence[str]] = None,
    archive_files: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    folder = os.path.abspath(folder)
    if direct_dicom_files is None or archive_files is None:
        scanned_direct, scanned_archives = scan_folder_inputs(folder)
        direct_dicom_files = scanned_direct
        archive_files = scanned_archives
    else:
        direct_dicom_files = list(direct_dicom_files)
        archive_files = list(archive_files)

    if not direct_dicom_files and not archive_files:
        raise ValueError(f"No processable DICOM slices or Vatech archives found in {folder}")

    logger.info(
        "Processing folder: %s (direct slices=%d, archives=%d)",
        folder,
        len(direct_dicom_files),
        len(archive_files),
    )

    results: List[str] = []
    created_names = set()

    if direct_dicom_files:
        direct_results = merge_direct_dicom_files(direct_dicom_files, folder)
        results.extend(direct_results)
        created_names.update(os.path.basename(path) for path in direct_results)

    for archive_path in archive_files:
        archive_results = process_vatech_archive(archive_path, folder, created_names)
        results.extend(archive_results)

    if output_dir and results:
        copy_to_output_dir(results, output_dir)

    return {
        "folder": folder,
        "results": results,
        "archive_names": [os.path.basename(path) for path in archive_files],
        "direct_dicom_count": len(direct_dicom_files),
    }


def print_folder_report(report: Dict[str, object], target_root: str) -> None:
    folder = os.path.abspath(str(report["folder"]))
    root = os.path.abspath(target_root)
    try:
        display_folder = os.path.relpath(folder, root)
    except ValueError:
        display_folder = folder
    if display_folder == ".":
        display_folder = os.path.basename(folder) or folder

    print(f"\n[OK] {display_folder}")
    print(
        f"  Direct slices: {report['direct_dicom_count']}, "
        f"Vatech archives: {len(report['archive_names'])}"
    )
    for result in report["results"]:
        size_mb = os.path.getsize(result) / 1024 / 1024
        print(f"  -> {os.path.basename(result)} ({size_mb:.1f} MB)")


def print_failed_folder(folder: str, exc: Exception) -> None:
    print(f"\n[FAILED] {folder}")
    print(f"  {exc}")


def run_once(target_path: str, recursive: bool, skip_if_done: bool, output_dir: str = "") -> int:
    processed_reports = []
    skipped = 0
    failures: List[Tuple[str, str]] = []
    found_candidates = False
    processable_found = 0

    for scanned_count, folder in iter_scan_roots(target_path, recursive, prune_done=skip_if_done):
        found_candidates = True
        if skip_if_done and is_already_done(folder):
            skipped += 1
            print(f"SKIPPED (already processed): {folder}")
            logger.debug("Skipping already processed folder: %s", folder)
            continue

        direct_dicom_files, archive_files = scan_folder_inputs(folder)
        if not direct_dicom_files and not archive_files:
            continue

        processable_found += 1
        logger.info(
            "Found processable folder #%d after scanning %d folder(s): %s "
            "(direct slices=%d, archives=%d)",
            processable_found,
            scanned_count,
            folder,
            len(direct_dicom_files),
            len(archive_files),
        )

        try:
            report = process_folder(
                folder,
                output_dir=output_dir,
                direct_dicom_files=direct_dicom_files,
                archive_files=archive_files,
            )
            processed_reports.append(report)
            print_folder_report(report, target_path)
            if skip_if_done:
                mark_as_done(folder, report)
        except Exception as exc:
            failures.append((folder, str(exc)))
            logger.error("Failed to process %s: %s", folder, exc)
            print_failed_folder(folder, exc)

    if processable_found == 0:
        if skip_if_done and skipped:
            print("Nothing new to process.")
            logger.info("Nothing new to process under %s", target_path)
            return 0
        if not found_candidates:
            print(f"ERROR: No Vatech archives or mergeable DICOM slices found in {target_path}")
            logger.warning("No processable folders found under %s", target_path)
            return 1
        print(f"ERROR: No Vatech archives or mergeable DICOM slices found in {target_path}")
        logger.warning("No processable folders found under %s", target_path)
        return 1

    if not processed_reports and skipped:
        print("Nothing new to process.")
        logger.info("Nothing new to process under %s", target_path)
        return 0

    total_outputs = sum(len(report["results"]) for report in processed_reports)
    print(
        f"\nProcessed {len(processed_reports)} folder(s), "
        f"created {total_outputs} merged file(s), skipped {skipped} folder(s)."
    )
    logger.info(
        "Run summary for %s: processed=%d output_files=%d skipped=%d failures=%d",
        target_path,
        len(processed_reports),
        total_outputs,
        skipped,
        len(failures),
    )
    if failures:
        print(f"Failed folders: {len(failures)}")
        return 1
    return 0


def run_watch(target_path: str, recursive: bool, interval: int, output_dir: str = "") -> int:
    print(f"Watch mode: scanning every {interval}s (Ctrl+C to stop)", flush=True)
    pass_number = 0
    try:
        while True:
            pass_number += 1
            pass_started = time.time()
            logger.info("Starting watch scan pass #%d under %s", pass_number, target_path)
            new_count = 0
            skipped_done = 0
            failed_count = 0
            discovered_count = 0

            for scanned_count, folder in iter_scan_roots(target_path, recursive, prune_done=True):
                if is_already_done(folder):
                    skipped_done += 1
                    logger.debug("Skipping already processed folder: %s", folder)
                    continue

                direct_dicom_files, archive_files = scan_folder_inputs(folder)
                if not direct_dicom_files and not archive_files:
                    continue

                discovered_count += 1
                logger.info(
                    "Found processable folder #%d after scanning %d folder(s): %s "
                    "(direct slices=%d, archives=%d)",
                    discovered_count,
                    scanned_count,
                    folder,
                    len(direct_dicom_files),
                    len(archive_files),
                )

                try:
                    report = process_folder(
                        folder,
                        output_dir=output_dir,
                        direct_dicom_files=direct_dicom_files,
                        archive_files=archive_files,
                    )
                    print_folder_report(report, target_path)
                    mark_as_done(folder, report)
                    new_count += 1
                except Exception as exc:
                    failed_count += 1
                    logger.error("Failed to process %s: %s", folder, exc)
                    print_failed_folder(folder, exc)

            pass_elapsed = time.time() - pass_started
            logger.info(
                "Completed watch scan pass #%d: discovered=%d new=%d skipped_done=%d "
                "failed=%d elapsed=%.1fs",
                pass_number,
                discovered_count,
                new_count,
                skipped_done,
                failed_count,
                pass_elapsed,
            )

            sleep_seconds = max(0.0, interval - pass_elapsed)
            if new_count == 0 and failed_count == 0:
                if sleep_seconds > 0:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] No new folders. "
                        f"Waiting {int(round(sleep_seconds))}s...",
                    )
                else:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] No new folders. "
                        "Starting the next scan immediately.",
                    )
            else:
                print(
                    f"[{time.strftime('%H:%M:%S')}] Pass #{pass_number}: processed {new_count} "
                    f"new folder(s), skipped {skipped_done}, failed {failed_count}."
                )

            if sleep_seconds > 0:
                logger.info(
                    "Waiting %.1fs before watch scan pass #%d",
                    sleep_seconds,
                    pass_number + 1,
                )
                time.sleep(sleep_seconds)
            else:
                logger.info(
                    "Scan pass #%d took %.1fs which exceeded interval %ss; "
                    "starting the next pass immediately",
                    pass_number,
                    pass_elapsed,
                    interval,
                )
    except KeyboardInterrupt:
        print("\nWatch mode stopped.")
        logger.info("Watch mode stopped by user")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dicompressor-vatech",
        description=f"{PROGRAM_NAME} v{VERSION}",
        add_help=False,
    )

    path_group = parser.add_mutually_exclusive_group()
    path_group.add_argument(
        "-f",
        dest="path_with_sub",
        metavar="PATH",
        help="Working folder (scan subfolders recursively)",
    )
    path_group.add_argument(
        "-F",
        dest="path_no_sub",
        metavar="PATH",
        help="Working folder only (do not scan subfolders)",
    )

    parser.add_argument("-h", dest="show_help", action="store_true", help="Display help text")
    parser.add_argument("-c", dest="show_version", action="store_true", help="Display version")
    parser.add_argument("-j", dest="merge", action="store_true", help="Run Vatech merge workflow")
    parser.add_argument(
        "--skip-if-done",
        dest="skip_if_done",
        action="store_true",
        help=f"Skip folders that already contain {DONE_MARKER}",
    )
    parser.add_argument(
        "--watch",
        dest="watch_interval",
        metavar="SECONDS",
        type=int,
        help="Watch mode: re-scan every N seconds and process only new folders",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        metavar="DIR",
        help="Copy merged result files to DIR after each successful merge",
    )
    parser.add_argument(
        "--log-file",
        dest="log_file",
        metavar="FILE",
        help=f"Write logs to FILE (default: {default_log_path()})",
    )
    parser.add_argument("--verbose", dest="verbose", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", dest="quiet", action="store_true", help="Suppress info output")

    args = parser.parse_args()

    if args.show_help or len(sys.argv) == 1:
        print_help_detailed()
        return 0

    if args.show_version:
        print(f"{PROGRAM_NAME} version {VERSION}")
        print("Dedicated workflow for Vatech DCM_FILE.CT archives")
        print(f"Python {sys.version}")
        return 0

    log_file = configure_logging(args.verbose, args.quiet, args.log_file or "")
    print(f"Log file: {log_file}", flush=True)
    logger.info("Starting %s v%s", PROGRAM_NAME, VERSION)
    logger.info("Log file: %s", log_file)

    if not args.merge:
        print("ERROR: This script currently supports only the merge workflow (-j).")
        print("Use -h for help.")
        return 1

    if args.path_with_sub:
        target_path = os.path.abspath(args.path_with_sub)
        recursive = True
    elif args.path_no_sub:
        target_path = os.path.abspath(args.path_no_sub)
        recursive = False
    else:
        print("ERROR: You must specify a folder with -f or -F")
        return 1

    if not os.path.isdir(target_path):
        print(f"ERROR: Folder does not exist: {target_path}")
        return 1

    output_dir = ""
    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        print(f"Output directory: {output_dir}", flush=True)

    start_time = time.time()

    try:
        if args.watch_interval:
            args.skip_if_done = True
            exit_code = run_watch(
                target_path=target_path,
                recursive=recursive,
                interval=args.watch_interval,
                output_dir=output_dir,
            )
        else:
            exit_code = run_once(
                target_path=target_path,
                recursive=recursive,
                skip_if_done=args.skip_if_done,
                output_dir=output_dir,
            )
    except Exception as exc:
        print(f"\nERROR: {exc}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1

    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.2f} seconds")
    logger.info("Completed in %.2f seconds with exit code %d", elapsed, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
