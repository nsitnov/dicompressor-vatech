"""
DICOM Utility Library - Core operations for DicomPressor
Cross-platform DICOM tool (macOS, Windows PowerShell, WSL/Linux)
Analogous to Sante Dicommander functionality
"""

import os
import sys
import struct
import copy
import uuid
import datetime
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

try:
    import pydicom
    from pydicom.dataset import Dataset, FileDataset
    from pydicom.sequence import Sequence
    from pydicom.uid import (
        ExplicitVRLittleEndian,
        ImplicitVRLittleEndian,
        ExplicitVRBigEndian,
        PYDICOM_IMPLEMENTATION_UID,
    )
    from pydicom.encaps import encapsulate
    from pydicom.pixel_data_handlers.util import convert_color_space
except ImportError:
    print("ERROR: pydicom is required. Install with: pip install pydicom")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger("dicompressor")

# Transfer Syntax UIDs
TS_IMPLICIT_VR_LE = "1.2.840.10008.1.2"
TS_EXPLICIT_VR_LE = "1.2.840.10008.1.2.1"
TS_EXPLICIT_VR_BE = "1.2.840.10008.1.2.2"
TS_JPEG_BASELINE = "1.2.840.10008.1.2.4.50"
TS_JPEG_EXTENDED = "1.2.840.10008.1.2.4.51"
TS_JPEG_LOSSLESS = "1.2.840.10008.1.2.4.57"
TS_JPEG_LOSSLESS_SV1 = "1.2.840.10008.1.2.4.70"
TS_JPEG2000_LOSSLESS = "1.2.840.10008.1.2.4.90"
TS_JPEG2000 = "1.2.840.10008.1.2.4.91"
TS_RLE = "1.2.840.10008.1.2.5"

COMPRESSED_SYNTAXES = {
    TS_JPEG_BASELINE, TS_JPEG_EXTENDED, TS_JPEG_LOSSLESS,
    TS_JPEG_LOSSLESS_SV1, TS_JPEG2000_LOSSLESS, TS_JPEG2000, TS_RLE
}

# SOP Class UIDs
SOP_CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2"
SOP_ENHANCED_CT = "1.2.840.10008.5.1.4.1.1.2.1"


def generate_uid() -> str:
    """Generate a unique DICOM UID."""
    return pydicom.uid.generate_uid()


def is_dicom_file(filepath: str) -> bool:
    """Check if a file is a valid DICOM file."""
    try:
        with open(filepath, 'rb') as f:
            # Check for DICOM preamble
            f.seek(128)
            magic = f.read(4)
            if magic == b'DICM':
                return True
            # Try reading without preamble (NEMA2)
            f.seek(0)
            try:
                pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
                return True
            except Exception:
                return False
    except Exception:
        return False


def find_dicom_files(folder: str, include_subfolders: bool = True) -> List[str]:
    """Find all DICOM files in a folder."""
    dicom_files = []
    if include_subfolders:
        for root, dirs, files in os.walk(folder):
            for f in sorted(files):
                fpath = os.path.join(root, f)
                if is_dicom_file(fpath):
                    dicom_files.append(fpath)
    else:
        for f in sorted(os.listdir(folder)):
            fpath = os.path.join(folder, f)
            if os.path.isfile(fpath) and is_dicom_file(fpath):
                dicom_files.append(fpath)
    return dicom_files


def find_image_files(folder: str, include_subfolders: bool = True) -> List[str]:
    """Find all image files in a folder."""
    image_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.jp2'}
    image_files = []
    if include_subfolders:
        for root, dirs, files in os.walk(folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in image_exts:
                    image_files.append(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(folder)):
            fpath = os.path.join(folder, f)
            if os.path.isfile(fpath) and Path(f).suffix.lower() in image_exts:
                image_files.append(fpath)
    return image_files


def find_video_files(folder: str, include_subfolders: bool = True) -> List[str]:
    """Find all video files in a folder."""
    video_exts = {'.avi', '.mp4', '.wmv', '.mov', '.mkv'}
    video_files = []
    if include_subfolders:
        for root, dirs, files in os.walk(folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in video_exts:
                    video_files.append(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(folder)):
            fpath = os.path.join(folder, f)
            if os.path.isfile(fpath) and Path(f).suffix.lower() in video_exts:
                video_files.append(fpath)
    return video_files


def get_output_path(original_path: str, overwrite: bool = False, suffix: str = "_mod") -> str:
    """Get the output path - either overwrite or add suffix."""
    if overwrite:
        return original_path
    base, ext = os.path.splitext(original_path)
    return f"{base}{suffix}{ext}"


def group_by_series(dicom_files: List[str]) -> Dict[str, List[str]]:
    """Group DICOM files by SeriesInstanceUID."""
    series_map = {}
    for fpath in dicom_files:
        try:
            ds = pydicom.dcmread(fpath, stop_before_pixels=True)
            series_uid = str(getattr(ds, 'SeriesInstanceUID', 'unknown'))
            if series_uid not in series_map:
                series_map[series_uid] = []
            series_map[series_uid].append(fpath)
        except Exception as e:
            logger.warning(f"Could not read {fpath}: {e}")
    return series_map


def get_pixel_array(ds: Dataset) -> 'np.ndarray':
    """Get pixel array from a dataset, handling compressed data."""
    if np is None:
        raise ImportError("numpy is required for pixel operations")
    return ds.pixel_array


# ============================================================
# 1. ANONYMIZE DICOM FILES
# ============================================================

def _parse_tag_line(line: str):
    """
    Parse a tag assignment line in one of two formats:
      - Simple:   (GGGG,EEEE)=VALUE       or  (GGGG,EEEE)=    (clear value)
      - Legacy:   GROUP ELEMENT [VR VALUE]  (space-separated hex)
    Returns (tag, vr_or_None, value_or_empty) or None if unparseable.
    """
    import re
    # Try (GGGG,EEEE)=VALUE format first
    m = re.match(r'^\(([0-9A-Fa-f]{4}),\s*([0-9A-Fa-f]{4})\)\s*=\s*(.*)', line)
    if m:
        group = int(m.group(1), 16)
        element = int(m.group(2), 16)
        tag = pydicom.tag.Tag(group, element)
        value = m.group(3).strip()
        return tag, None, value

    # Legacy space-separated format: GROUP ELEMENT [VR] [VALUE...]
    parts = line.split()
    if len(parts) >= 2:
        try:
            group = int(parts[0], 16)
            element = int(parts[1], 16)
            tag = pydicom.tag.Tag(group, element)
            if len(parts) >= 4:
                vr = parts[2]
                value = ' '.join(parts[3:])
                return tag, vr, value
            elif len(parts) >= 3:
                vr = parts[2]
                return tag, vr, ''
            else:
                return tag, None, ''
        except ValueError:
            pass

    return None


def parse_anonymize_params(param_file: str) -> Tuple[List[Dict], List[Dict]]:
    """
    Parse anonymization parameter file.
    Returns (rectangles, tag_operations)

    Supports two tag formats:
      - (GGGG,EEEE)=VALUE       e.g. (0010,0010)=ANONYMOUS
      - GROUP ELEMENT [VR VALUE] e.g. 0010 0010 PN ANONYMOUS

    Rectangle format: [type] [left] [top] [right] [bottom]
    """
    rectangles = []
    tag_operations = []

    with open(param_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split()
            if parts[0] in ('r', 'ri', 'rv'):
                # Rectangle definition
                if len(parts) >= 5:
                    rectangles.append({
                        'type': parts[0],
                        'left': int(parts[1]),
                        'top': int(parts[2]),
                        'right': int(parts[3]),
                        'bottom': int(parts[4])
                    })
            else:
                result = _parse_tag_line(line)
                if result:
                    tag, vr, value = result
                    tag_operations.append({'tag': tag, 'vr': vr, 'value': value})
                else:
                    logger.warning(f"Cannot parse anonymize line: {line}")

    return rectangles, tag_operations


def apply_rectangles(ds: Dataset, rectangles: List[Dict]) -> Dataset:
    """Apply rectangle masks to pixel data for burned-in annotation removal."""
    if np is None:
        logger.warning("numpy not available, skipping rectangle masks")
        return ds

    try:
        pixel_array = get_pixel_array(ds)
    except Exception as e:
        logger.warning(f"Cannot read pixel data: {e}")
        return ds

    num_frames = getattr(ds, 'NumberOfFrames', 1)
    if isinstance(num_frames, str):
        num_frames = int(num_frames)
    is_multiframe = num_frames > 1

    for rect in rectangles:
        rtype = rect['type']
        # Determine if this rect applies
        if rtype == 'r':
            apply = True
        elif rtype == 'ri' and not is_multiframe:
            apply = True
        elif rtype == 'rv' and is_multiframe:
            apply = True
        else:
            apply = False

        if not apply:
            continue

        left, top, right, bottom = rect['left'], rect['top'], rect['right'], rect['bottom']

        # Get fill color from upper-left pixel of rectangle
        if len(pixel_array.shape) == 2:
            fill_color = pixel_array[top, left] if top < pixel_array.shape[0] and left < pixel_array.shape[1] else 0
            pixel_array[top:bottom, left:right] = fill_color
        elif len(pixel_array.shape) == 3:
            if is_multiframe and pixel_array.shape[0] == num_frames:
                for frame_idx in range(num_frames):
                    fill_color = pixel_array[frame_idx, top, left] if top < pixel_array.shape[1] and left < pixel_array.shape[2] else 0
                    pixel_array[frame_idx, top:bottom, left:right] = fill_color
            else:
                fill_color = pixel_array[top, left] if top < pixel_array.shape[0] and left < pixel_array.shape[1] else 0
                pixel_array[top:bottom, left:right] = fill_color
        elif len(pixel_array.shape) == 4:
            for frame_idx in range(pixel_array.shape[0]):
                fill_color = pixel_array[frame_idx, top, left] if top < pixel_array.shape[1] and left < pixel_array.shape[2] else np.zeros(pixel_array.shape[3])
                pixel_array[frame_idx, top:bottom, left:right] = fill_color

    ds.PixelData = pixel_array.tobytes()
    return ds


def anonymize_file(filepath: str, param_file: str, overwrite: bool = False) -> str:
    """Anonymize a single DICOM file."""
    rectangles, tag_operations = parse_anonymize_params(param_file)

    ds = pydicom.dcmread(filepath)

    # Apply rectangle masks
    if rectangles:
        ds = apply_rectangles(ds, rectangles)

    # Apply tag operations
    for op in tag_operations:
        tag = op['tag']
        if op['value']:
            # Modify tag with new value
            if op['vr']:
                ds.add_new(tag, op['vr'], op['value'])
            else:
                if tag in ds:
                    ds[tag].value = op['value']
        else:
            # Remove tag value (set to empty)
            if tag in ds:
                ds[tag].value = ''

    output_path = get_output_path(filepath, overwrite, "_anon")
    ds.save_as(output_path)
    return output_path


def anonymize_folder(folder: str, param_file: str, include_subfolders: bool = True,
                     overwrite: bool = False) -> List[str]:
    """Anonymize all DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = anonymize_file(f, param_file, overwrite)
            results.append(out)
            logger.info(f"Anonymized: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to anonymize {f}: {e}")
    return results


# ============================================================
# 2. MODIFY DICOM TAGS
# ============================================================

def parse_modify_params(param_file: str) -> List[Dict]:
    """
    Parse modification parameter file.

    Supports two formats:
      - Simple:  (GGGG,EEEE)=VALUE       → auto-detects insert vs modify
      - Legacy:  ACTION GROUP ELEMENT [VR VALUE]
                 Actions: i (insert), m (modify), r (remove)
    """
    operations = []
    with open(param_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Try (GGGG,EEEE)=VALUE format
            result = _parse_tag_line(line)
            if result and line.startswith('('):
                tag, vr, value = result
                # Treat as modify/insert
                operations.append({'action': 'm', 'tag': tag, 'vr': vr, 'value': value})
                continue

            # Legacy format: action group element [VR] [value...]
            parts = line.split()
            if len(parts) < 3:
                logger.warning(f"Invalid modification line: {line}")
                continue
            try:
                action = parts[0]
                group = int(parts[1], 16)
                element = int(parts[2], 16)
                tag = pydicom.tag.Tag(group, element)

                if action in ('i', 'm') and len(parts) >= 5:
                    vr = parts[3]
                    value = ' '.join(parts[4:])
                    operations.append({'action': action, 'tag': tag, 'vr': vr, 'value': value})
                elif action in ('i', 'm') and len(parts) >= 4:
                    vr = parts[3]
                    operations.append({'action': action, 'tag': tag, 'vr': vr, 'value': ''})
                elif action == 'r':
                    operations.append({'action': action, 'tag': tag, 'vr': None, 'value': None})
                else:
                    logger.warning(f"Invalid modification line: {line}")
            except ValueError:
                logger.warning(f"Cannot parse modification line: {line}")

    return operations


def modify_file(filepath: str, param_file: str, overwrite: bool = False) -> str:
    """Modify tags in a single DICOM file."""
    operations = parse_modify_params(param_file)
    ds = pydicom.dcmread(filepath)

    for op in operations:
        tag = op['tag']
        if op['action'] == 'r':
            if tag in ds:
                del ds[tag]
        elif op['action'] == 'm':
            if tag in ds:
                if op['vr']:
                    ds[tag].VR = op['vr']
                ds[tag].value = _convert_value(op['vr'], op['value'])
            else:
                ds.add_new(tag, op['vr'], _convert_value(op['vr'], op['value']))
        elif op['action'] == 'i':
            ds.add_new(tag, op['vr'], _convert_value(op['vr'], op['value']))

    output_path = get_output_path(filepath, overwrite, "_mod")
    ds.save_as(output_path)
    return output_path


def _convert_value(vr: str, value: str) -> Any:
    """Convert string value to appropriate type based on VR."""
    if not value:
        return ''
    if vr in ('DS', 'FD', 'FL'):
        try:
            return float(value)
        except ValueError:
            return value
    elif vr in ('IS', 'SL', 'SS', 'UL', 'US'):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def modify_folder(folder: str, param_file: str, include_subfolders: bool = True,
                  overwrite: bool = False) -> List[str]:
    """Modify tags in all DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = modify_file(f, param_file, overwrite)
            results.append(out)
            logger.info(f"Modified: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to modify {f}: {e}")
    return results


# ============================================================
# 3. CONVERT PLAIN IMAGES TO DICOM
# ============================================================

def image_to_dicom(image_path: str, output_path: Optional[str] = None) -> str:
    """Convert a plain image to a DICOM file."""
    if Image is None:
        raise ImportError("Pillow is required for image conversion. Install with: pip install Pillow")
    if np is None:
        raise ImportError("numpy is required for image conversion.")

    img = Image.open(image_path)

    if output_path is None:
        output_path = str(Path(image_path).with_suffix('.dcm'))

    # Create DICOM dataset
    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7'  # Secondary Capture
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\x00" * 128)

    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyDate = datetime.datetime.now().strftime('%Y%m%d')
    ds.Modality = 'OT'
    ds.Manufacturer = 'DicomPressor'
    ds.PatientName = ''
    ds.PatientID = ''

    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()

    if img.mode == 'L':
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        pixel_array = np.array(img)
    elif img.mode in ('RGB', 'RGBA'):
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = 'RGB'
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.PlanarConfiguration = 0
        pixel_array = np.array(img)
    else:
        img = img.convert('L')
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        pixel_array = np.array(img)

    ds.Rows, ds.Columns = pixel_array.shape[:2]
    ds.PixelData = pixel_array.tobytes()
    ds.NumberOfFrames = 1

    ds.save_as(output_path)
    return output_path


def images_to_dicom_folder(folder: str, include_subfolders: bool = True,
                           single_multiframe: bool = False) -> List[str]:
    """Convert all images in a folder to DICOM."""
    image_files = find_image_files(folder, include_subfolders)

    if single_multiframe and image_files:
        return [images_to_multiframe_dicom(image_files, folder)]

    results = []
    for img_path in image_files:
        try:
            out = image_to_dicom(img_path)
            results.append(out)
            logger.info(f"Converted image: {img_path} -> {out}")
        except Exception as e:
            logger.error(f"Failed to convert {img_path}: {e}")
    return results


def images_to_multiframe_dicom(image_files: List[str], output_folder: str) -> str:
    """Convert multiple images to a single multi-frame DICOM file."""
    if Image is None or np is None:
        raise ImportError("Pillow and numpy are required")

    frames = []
    for img_path in image_files:
        img = Image.open(img_path).convert('L')
        frames.append(np.array(img))

    if not frames:
        raise ValueError("No images to convert")

    # Ensure all frames are the same size
    target_shape = frames[0].shape
    for i in range(len(frames)):
        if frames[i].shape != target_shape:
            img = Image.fromarray(frames[i]).resize((target_shape[1], target_shape[0]))
            frames[i] = np.array(img)

    pixel_array = np.stack(frames, axis=0)

    output_path = os.path.join(output_folder, "multiframe.dcm")

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyDate = datetime.datetime.now().strftime('%Y%m%d')
    ds.Modality = 'OT'
    ds.Manufacturer = 'DicomPressor'
    ds.PatientName = ''
    ds.PatientID = ''
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.Rows, ds.Columns = target_shape
    ds.NumberOfFrames = len(frames)
    ds.PixelData = pixel_array.tobytes()

    ds.save_as(output_path)
    return output_path


# ============================================================
# 4. CONVERT VIDEOS TO DICOM
# ============================================================

def video_to_dicom(video_path: str, output_path: Optional[str] = None) -> str:
    """Convert a video file to a compressed multi-frame DICOM file."""
    try:
        import cv2
    except ImportError:
        raise ImportError("opencv-python is required for video conversion. Install with: pip install opencv-python")

    if np is None:
        raise ImportError("numpy is required")

    if output_path is None:
        output_path = str(Path(video_path).with_suffix('.dcm'))

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frames.append(gray)
    cap.release()

    if not frames:
        raise ValueError("No frames in video")

    pixel_array = np.stack(frames, axis=0)
    rows, cols = frames[0].shape

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyDate = datetime.datetime.now().strftime('%Y%m%d')
    ds.Modality = 'OT'
    ds.Manufacturer = 'DicomPressor'
    ds.PatientName = ''
    ds.PatientID = ''
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.NumberOfFrames = len(frames)
    ds.PixelData = pixel_array.tobytes()

    ds.save_as(output_path)
    return output_path


def videos_to_dicom_folder(folder: str, include_subfolders: bool = True) -> List[str]:
    """Convert all videos in a folder to DICOM."""
    video_files = find_video_files(folder, include_subfolders)
    results = []
    for vf in video_files:
        try:
            out = video_to_dicom(vf)
            results.append(out)
            logger.info(f"Converted video: {vf} -> {out}")
        except Exception as e:
            logger.error(f"Failed to convert video {vf}: {e}")
    return results


# ============================================================
# 5. EXPORT DICOM TO PLAIN IMAGES
# ============================================================

def dicom_to_images(filepath: str, image_type: str = 'jpeg',
                    burn_annotations: bool = False,
                    output_folder: Optional[str] = None) -> List[str]:
    """Export DICOM file to plain image(s)."""
    if Image is None or np is None:
        raise ImportError("Pillow and numpy required for image export")

    ds = pydicom.dcmread(filepath)
    pixel_array = get_pixel_array(ds)

    if output_folder is None:
        output_folder = os.path.dirname(filepath)

    basename = Path(filepath).stem

    ext_map = {
        'jpeg': '.jpg', 'jpg': '.jpg',
        'jpeg2000': '.jp2', 'jp2': '.jp2',
        'bmp': '.bmp',
        'tiff': '.tiff', 'tif': '.tiff',
        'png': '.png'
    }
    ext = ext_map.get(image_type.lower(), '.jpg')

    num_frames = getattr(ds, 'NumberOfFrames', 1)
    if isinstance(num_frames, str):
        num_frames = int(num_frames)

    results = []

    if num_frames > 1:
        for i in range(num_frames):
            frame = pixel_array[i]
            frame = _normalize_pixel_data(frame)
            img = Image.fromarray(frame)

            if burn_annotations:
                img = _burn_annotations(img, ds)

            out_path = os.path.join(output_folder, f"{basename}_frame{i:04d}{ext}")
            img.save(out_path)
            results.append(out_path)
    else:
        frame = _normalize_pixel_data(pixel_array)
        img = Image.fromarray(frame)

        if burn_annotations:
            img = _burn_annotations(img, ds)

        out_path = os.path.join(output_folder, f"{basename}{ext}")
        img.save(out_path)
        results.append(out_path)

    return results


def _normalize_pixel_data(pixel_array: 'np.ndarray') -> 'np.ndarray':
    """Normalize pixel data to 8-bit for image export."""
    if pixel_array.dtype == np.uint8:
        return pixel_array

    # Normalize to 0-255
    pmin, pmax = pixel_array.min(), pixel_array.max()
    if pmax > pmin:
        normalized = ((pixel_array - pmin) / (pmax - pmin) * 255).astype(np.uint8)
    else:
        normalized = np.zeros_like(pixel_array, dtype=np.uint8)
    return normalized


def _burn_annotations(img: 'Image.Image', ds: Dataset) -> 'Image.Image':
    """Burn patient info annotations onto image."""
    try:
        from PIL import ImageDraw, ImageFont
    except ImportError:
        return img

    draw = ImageDraw.Draw(img)

    patient_name = str(getattr(ds, 'PatientName', ''))
    patient_id = str(getattr(ds, 'PatientID', ''))
    study_date = str(getattr(ds, 'StudyDate', ''))

    text = f"{patient_name} | {patient_id} | {study_date}"

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except (OSError, IOError):
        font = ImageFont.load_default()

    draw.text((10, 10), text, fill=255 if img.mode == 'L' else (255, 255, 255), font=font)
    return img


def export_images_folder(folder: str, image_type: str = 'jpeg',
                         include_subfolders: bool = True,
                         burn_annotations: bool = False) -> List[str]:
    """Export all DICOM files in a folder to images."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            outs = dicom_to_images(f, image_type, burn_annotations)
            results.extend(outs)
            logger.info(f"Exported images from: {f}")
        except Exception as e:
            logger.error(f"Failed to export {f}: {e}")
    return results


# ============================================================
# 6. EXPORT DICOM TO VIDEO
# ============================================================

def dicom_to_video(filepath: str, burn_annotations: bool = False,
                   output_path: Optional[str] = None) -> str:
    """Export multi-frame DICOM to video (mp4/avi)."""
    try:
        import cv2
    except ImportError:
        raise ImportError("opencv-python required for video export. Install with: pip install opencv-python")

    if np is None:
        raise ImportError("numpy is required")

    ds = pydicom.dcmread(filepath)
    pixel_array = get_pixel_array(ds)

    num_frames = getattr(ds, 'NumberOfFrames', 1)
    if isinstance(num_frames, str):
        num_frames = int(num_frames)

    if num_frames <= 1:
        raise ValueError(f"File has only {num_frames} frame(s), need multi-frame for video")

    if output_path is None:
        output_path = str(Path(filepath).with_suffix('.avi'))

    rows = ds.Rows
    cols = ds.Columns

    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    fps = 30
    out = cv2.VideoWriter(output_path, fourcc, fps, (cols, rows), False)

    for i in range(num_frames):
        frame = _normalize_pixel_data(pixel_array[i])

        if burn_annotations:
            patient_name = str(getattr(ds, 'PatientName', ''))
            patient_id = str(getattr(ds, 'PatientID', ''))
            cv2.putText(frame, f"{patient_name} | {patient_id}", (10, 25),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, 255, 1)

        out.write(frame)

    out.release()
    return output_path


def export_video_folder(folder: str, include_subfolders: bool = True,
                        burn_annotations: bool = False) -> List[str]:
    """Export all multi-frame DICOM files in a folder to video."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = dicom_to_video(f, burn_annotations)
            results.append(out)
            logger.info(f"Exported video from: {f}")
        except ValueError:
            pass  # Single-frame files
        except Exception as e:
            logger.error(f"Failed to export video {f}: {e}")
    return results


# ============================================================
# 7. CREATE DICOMDIR
# ============================================================

def create_dicomdir(folder: str, include_subfolders: bool = True,
                    save_in_parent: bool = False) -> str:
    """Create a DICOMDIR file from DICOM files in a folder."""
    if save_in_parent:
        output_folder = str(Path(folder).parent)
    else:
        output_folder = folder

    dicomdir_path = os.path.join(output_folder, "DICOMDIR")

    dicom_files = find_dicom_files(folder, include_subfolders)
    if not dicom_files:
        raise ValueError(f"No DICOM files found in {folder}")

    # Build DICOMDIR structure
    patients = {}
    for fpath in dicom_files:
        try:
            ds = pydicom.dcmread(fpath, stop_before_pixels=True)
            patient_id = str(getattr(ds, 'PatientID', 'UNKNOWN'))
            patient_name = str(getattr(ds, 'PatientName', 'UNKNOWN'))
            study_uid = str(getattr(ds, 'StudyInstanceUID', ''))
            series_uid = str(getattr(ds, 'SeriesInstanceUID', ''))
            sop_uid = str(getattr(ds, 'SOPInstanceUID', ''))

            if patient_id not in patients:
                patients[patient_id] = {
                    'name': patient_name,
                    'studies': {}
                }

            if study_uid not in patients[patient_id]['studies']:
                patients[patient_id]['studies'][study_uid] = {
                    'date': str(getattr(ds, 'StudyDate', '')),
                    'series': {}
                }

            if series_uid not in patients[patient_id]['studies'][study_uid]['series']:
                patients[patient_id]['studies'][study_uid]['series'][series_uid] = {
                    'modality': str(getattr(ds, 'Modality', '')),
                    'images': []
                }

            rel_path = os.path.relpath(fpath, output_folder)
            patients[patient_id]['studies'][study_uid]['series'][series_uid]['images'].append({
                'path': rel_path.replace(os.sep, '\\'),
                'sop_uid': sop_uid,
                'sop_class': str(getattr(ds, 'SOPClassUID', ''))
            })
        except Exception as e:
            logger.warning(f"Skipping {fpath}: {e}")

    # Use pydicom to generate DICOMDIR
    try:
        from pydicom.fileset import FileSet
        fs = FileSet()
        for fpath in dicom_files:
            try:
                fs.add(fpath)
            except Exception as e:
                logger.warning(f"Could not add to DICOMDIR: {fpath}: {e}")

        fs.write(output_folder)
        logger.info(f"Created DICOMDIR at {dicomdir_path}")
    except (ImportError, Exception) as e:
        # Fallback: write a simple DICOMDIR manually
        logger.warning(f"Using basic DICOMDIR generation: {e}")
        _write_basic_dicomdir(dicomdir_path, patients)

    return dicomdir_path


def _write_basic_dicomdir(output_path: str, patients: Dict) -> None:
    """Write a basic DICOMDIR file."""
    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.1.3.10'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.SOPClassUID = '1.2.840.10008.1.3.10'
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    logger.info(f"Basic DICOMDIR written to: {output_path}")
    ds.save_as(output_path)


# ============================================================
# 8. MERGE SINGLE-FRAME TO MULTI-FRAME
# ============================================================

def merge_files_to_multiframe(dicom_files: List[str], output_folder: str,
                              raise_on_error: bool = False) -> List[str]:
    """Merge a pre-selected list of single-frame DICOM files into multi-frame files."""
    if np is None:
        raise ImportError("numpy is required for merge operation")
    if not dicom_files:
        raise ValueError("No DICOM files were provided for merge")

    # Group by series
    series_groups = group_by_series(dicom_files)

    results = []
    for series_uid, files in series_groups.items():
        try:
            out = _merge_series_to_multiframe(files, series_uid, output_folder)
            results.append(out)
            logger.info(f"Merged {len(files)} files into: {out}")
        except Exception as e:
            if raise_on_error:
                raise RuntimeError(f"Failed to merge series {series_uid}: {e}") from e
            logger.error(f"Failed to merge series {series_uid}: {e}")

    return results


def merge_to_multiframe(folder: str, include_subfolders: bool = True,
                        output_folder: Optional[str] = None,
                        raise_on_error: bool = False) -> List[str]:
    """
    Merge many single-frame DICOM files to one multi-frame DICOM file.
    Groups by SeriesInstanceUID - one multi-frame file per series.
    """
    dicom_files = find_dicom_files(folder, include_subfolders)
    if not dicom_files:
        raise ValueError(f"No DICOM files found in {folder}")

    if output_folder is None:
        output_folder = folder

    return merge_files_to_multiframe(dicom_files, output_folder, raise_on_error=raise_on_error)


def _merge_series_to_multiframe(files: List[str], series_uid: str,
                                 output_folder: str) -> str:
    """Merge a single series of DICOM files into one Enhanced CT multi-frame file.

    Uses Enhanced CT Image Storage SOP Class (1.2.840.10008.5.1.4.1.1.2.1) which
    properly supports PerFrameFunctionalGroupsSequence for correct 3D reconstruction
    in viewers like VolView, 3D Slicer, OsiriX, etc.
    """
    # Filter: only keep single-frame files (skip already-merged multi-frame files)
    single_frame_files = []
    for f in files:
        ds = pydicom.dcmread(f, stop_before_pixels=True)
        num_frames = getattr(ds, 'NumberOfFrames', 1)
        if isinstance(num_frames, str):
            num_frames = int(num_frames)
        if num_frames <= 1:
            single_frame_files.append(f)
        else:
            logger.info(f"Skipping multi-frame file: {f} ({num_frames} frames)")

    if not single_frame_files:
        raise ValueError("No single-frame files found to merge")

    # Read headers to get spatial info for proper sorting
    file_data = []
    for f in single_frame_files:
        ds = pydicom.dcmread(f, stop_before_pixels=True)
        instance_num = getattr(ds, 'InstanceNumber', 0)
        if isinstance(instance_num, str):
            try:
                instance_num = int(instance_num)
            except ValueError:
                instance_num = 0

        # Get slice position for spatial sorting
        slice_pos = None
        if hasattr(ds, 'ImagePositionPatient'):
            pos = [float(x) for x in ds.ImagePositionPatient]
            slice_pos = pos[2]  # Z-coordinate for axial slices
        elif hasattr(ds, 'SliceLocation'):
            slice_pos = float(ds.SliceLocation)

        file_data.append({
            'instance_num': instance_num,
            'slice_pos': slice_pos,
            'filepath': f
        })

    # Sort by slice position (ascending Z) if available, otherwise by instance number
    if all(fd['slice_pos'] is not None for fd in file_data):
        file_data.sort(key=lambda x: x['slice_pos'])
        logger.info("Sorting frames by slice position (Z-coordinate)")
    else:
        file_data.sort(key=lambda x: x['instance_num'])
        logger.info("Sorting frames by instance number")

    sorted_files = [fd['filepath'] for fd in file_data]

    # Calculate spacing between slices from first two files
    slice_spacing = None
    if len(file_data) >= 2 and file_data[0]['slice_pos'] is not None and file_data[1]['slice_pos'] is not None:
        slice_spacing = abs(file_data[1]['slice_pos'] - file_data[0]['slice_pos'])
        logger.info(f"Calculated SpacingBetweenSlices: {slice_spacing:.6f} mm")

    # Read all single-frame datasets with pixel data, collecting positions
    all_datasets = []
    template_ds = pydicom.dcmread(sorted_files[0])
    template_shape = (template_ds.Rows, template_ds.Columns)

    for f in sorted_files:
        ds = pydicom.dcmread(f)
        try:
            pixel_array = ds.pixel_array
            if pixel_array.shape[:2] == template_shape or \
               (len(pixel_array.shape) >= 2 and pixel_array.shape[-2:] == template_shape):
                all_datasets.append(ds)
            else:
                logger.warning(f"Skipping {f}: shape {pixel_array.shape} != template {template_shape}")
        except Exception as e:
            logger.warning(f"Skipping {f}: cannot read pixel data: {e}")

    if not all_datasets:
        raise ValueError("No valid frames found")

    num_frames = len(all_datasets)
    logger.info(f"Merging {num_frames} frames into Enhanced CT multi-frame")

    # Stack pixel data
    frames = [ds.pixel_array for ds in all_datasets]
    pixel_data = np.stack(frames, axis=0)

    # =========================================================
    # Build Enhanced CT Image Storage multi-frame dataset
    # SOP Class: 1.2.840.10008.5.1.4.1.1.2.1
    # =========================================================
    out_ds = Dataset()

    # --- File Meta Information ---
    file_meta = pydicom.dataset.FileMetaDataset()
    file_meta.FileMetaInformationVersion = b'\x00\x01'
    file_meta.MediaStorageSOPClassUID = SOP_ENHANCED_CT
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    out_ds.file_meta = file_meta
    out_ds.is_little_endian = True
    out_ds.is_implicit_VR = False

    # --- Patient Module (copy from template) ---
    for tag_name in ['PatientName', 'PatientID', 'PatientBirthDate', 'PatientSex',
                     'PatientAge', 'PatientWeight']:
        if hasattr(template_ds, tag_name):
            setattr(out_ds, tag_name, getattr(template_ds, tag_name))

    # --- General Study Module ---
    for tag_name in ['StudyInstanceUID', 'StudyDate', 'StudyTime', 'StudyID',
                     'AccessionNumber', 'ReferringPhysicianName', 'StudyDescription']:
        if hasattr(template_ds, tag_name):
            setattr(out_ds, tag_name, getattr(template_ds, tag_name))

    # --- General Series Module ---
    for tag_name in ['Modality', 'SeriesInstanceUID', 'SeriesNumber', 'SeriesDate',
                     'SeriesTime', 'SeriesDescription', 'BodyPartExamined',
                     'ProtocolName', 'PerformingPhysicianName']:
        if hasattr(template_ds, tag_name):
            setattr(out_ds, tag_name, getattr(template_ds, tag_name))

    # --- Frame of Reference Module ---
    for tag_name in ['FrameOfReferenceUID', 'PositionReferenceIndicator']:
        if hasattr(template_ds, tag_name):
            setattr(out_ds, tag_name, getattr(template_ds, tag_name))

    # --- General Equipment Module ---
    for tag_name in ['Manufacturer', 'InstitutionName', 'StationName',
                     'ManufacturerModelName', 'DeviceSerialNumber',
                     'SoftwareVersions']:
        if hasattr(template_ds, tag_name):
            setattr(out_ds, tag_name, getattr(template_ds, tag_name))

    # --- Enhanced CT SOP Class specific ---
    out_ds.SOPClassUID = SOP_ENHANCED_CT
    out_ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    out_ds.InstanceNumber = 1
    out_ds.ContentDate = datetime.datetime.now().strftime('%Y%m%d')
    out_ds.ContentTime = datetime.datetime.now().strftime('%H%M%S.%f')
    out_ds.ImageType = ['ORIGINAL', 'PRIMARY', 'VOLUME', 'NONE']
    out_ds.AcquisitionNumber = 1

    # --- Multi-frame Module ---
    out_ds.NumberOfFrames = num_frames
    out_ds.InstanceNumber = 1

    # --- Image Pixel Module ---
    out_ds.SamplesPerPixel = int(getattr(template_ds, 'SamplesPerPixel', 1))
    out_ds.PhotometricInterpretation = str(getattr(template_ds, 'PhotometricInterpretation', 'MONOCHROME2'))
    out_ds.Rows = template_ds.Rows
    out_ds.Columns = template_ds.Columns
    out_ds.BitsAllocated = int(getattr(template_ds, 'BitsAllocated', 16))
    out_ds.BitsStored = int(getattr(template_ds, 'BitsStored', 16))
    out_ds.HighBit = int(getattr(template_ds, 'HighBit', 15))
    out_ds.PixelRepresentation = int(getattr(template_ds, 'PixelRepresentation', 0))
    if hasattr(template_ds, 'RescaleIntercept'):
        out_ds.RescaleIntercept = template_ds.RescaleIntercept
    if hasattr(template_ds, 'RescaleSlope'):
        out_ds.RescaleSlope = template_ds.RescaleSlope
    if hasattr(template_ds, 'RescaleType'):
        out_ds.RescaleType = template_ds.RescaleType
    if hasattr(template_ds, 'WindowCenter'):
        out_ds.WindowCenter = template_ds.WindowCenter
    if hasattr(template_ds, 'WindowWidth'):
        out_ds.WindowWidth = template_ds.WindowWidth

    # Pixel data
    out_ds.PixelData = pixel_data.tobytes()

    # --- Multi-frame Dimension Module ---
    # DimensionOrganizationSequence - tells viewer how frames are organized
    dim_org_uid = generate_uid()
    dim_org_item = Dataset()
    dim_org_item.DimensionOrganizationUID = dim_org_uid
    out_ds.DimensionOrganizationSequence = Sequence([dim_org_item])
    out_ds.DimensionOrganizationType = "3D"

    # DimensionIndexSequence - ImagePositionPatient Z-axis index
    dim_idx = Dataset()
    dim_idx.DimensionOrganizationUID = dim_org_uid
    # Pointer to ImagePositionPatient tag (0020,0032)
    dim_idx.DimensionIndexPointer = pydicom.tag.Tag(0x0020, 0x0032)
    # Pointer to PlanePositionSequence (0020,9113) as the functional group
    dim_idx.FunctionalGroupPointer = pydicom.tag.Tag(0x0020, 0x9113)
    out_ds.DimensionIndexSequence = Sequence([dim_idx])

    # =========================================================
    # Shared Functional Groups Sequence
    # =========================================================
    shared_fg = Dataset()

    # PixelMeasuresSequence — spacing info shared across all frames
    pixel_measures = Dataset()
    pixel_spacing = getattr(template_ds, 'PixelSpacing', [1.0, 1.0])
    pixel_measures.PixelSpacing = [float(pixel_spacing[0]), float(pixel_spacing[1])]
    if slice_spacing is not None and slice_spacing > 0:
        pixel_measures.SpacingBetweenSlices = float(slice_spacing)
        pixel_measures.SliceThickness = float(slice_spacing)
    elif hasattr(template_ds, 'SliceThickness'):
        pixel_measures.SliceThickness = float(template_ds.SliceThickness)
        pixel_measures.SpacingBetweenSlices = float(template_ds.SliceThickness)
    shared_fg.PixelMeasuresSequence = Sequence([pixel_measures])

    # PlaneOrientationSequence — image orientation shared across all frames
    if hasattr(template_ds, 'ImageOrientationPatient'):
        plane_orient = Dataset()
        iop = template_ds.ImageOrientationPatient
        plane_orient.ImageOrientationPatient = [float(x) for x in iop]
        shared_fg.PlaneOrientationSequence = Sequence([plane_orient])

    # CT Image Frame Type Sequence
    ct_frame_type = Dataset()
    ct_frame_type.FrameType = ['ORIGINAL', 'PRIMARY', 'VOLUME', 'NONE']
    ct_frame_type.PixelPresentation = 'MONOCHROME'
    ct_frame_type.VolumetricProperties = 'VOLUME'
    ct_frame_type.VolumeBasedCalculationTechnique = 'NONE'
    shared_fg.CTImageFrameTypeSequence = Sequence([ct_frame_type])

    # PixelValueTransformationSequence (rescale info)
    if hasattr(template_ds, 'RescaleIntercept') and hasattr(template_ds, 'RescaleSlope'):
        pv_transform = Dataset()
        pv_transform.RescaleIntercept = str(float(template_ds.RescaleIntercept))
        pv_transform.RescaleSlope = str(float(template_ds.RescaleSlope))
        pv_transform.RescaleType = str(getattr(template_ds, 'RescaleType', 'HU'))
        shared_fg.PixelValueTransformationSequence = Sequence([pv_transform])

    # Frame VOI LUT Sequence (windowing)
    if hasattr(template_ds, 'WindowCenter') and hasattr(template_ds, 'WindowWidth'):
        voi_lut = Dataset()
        wc = template_ds.WindowCenter
        ww = template_ds.WindowWidth
        voi_lut.WindowCenter = float(wc) if not isinstance(wc, (list, pydicom.multival.MultiValue)) else float(wc[0])
        voi_lut.WindowWidth = float(ww) if not isinstance(ww, (list, pydicom.multival.MultiValue)) else float(ww[0])
        voi_lut.WindowCenterWidthExplanation = 'NORMAL'
        voi_lut.VOILUTFunction = 'LINEAR'
        shared_fg.FrameVOILUTSequence = Sequence([voi_lut])

    out_ds.SharedFunctionalGroupsSequence = Sequence([shared_fg])

    # =========================================================
    # Per-Frame Functional Groups Sequence
    # =========================================================
    per_frame_seq = []
    for i, ds in enumerate(all_datasets):
        frame_item = Dataset()

        # PlanePositionSequence — unique position for each frame
        plane_pos = Dataset()
        if hasattr(ds, 'ImagePositionPatient'):
            plane_pos.ImagePositionPatient = [float(x) for x in ds.ImagePositionPatient]
        else:
            # Synthesize position from slice index and spacing
            z_offset = i * (slice_spacing if slice_spacing else 1.0)
            plane_pos.ImagePositionPatient = [0.0, 0.0, z_offset]
        frame_item.PlanePositionSequence = Sequence([plane_pos])

        # FrameContentSequence — required for Enhanced multi-frame
        frame_content = Dataset()
        frame_content.FrameAcquisitionNumber = i + 1
        frame_content.FrameReferenceDateTime = out_ds.ContentDate + out_ds.ContentTime
        frame_content.FrameAcquisitionDateTime = out_ds.ContentDate + out_ds.ContentTime
        frame_content.FrameAcquisitionDuration = 0.0
        frame_content.DimensionIndexValues = [i + 1]
        frame_item.FrameContentSequence = Sequence([frame_content])

        per_frame_seq.append(frame_item)

    out_ds.PerFrameFunctionalGroupsSequence = Sequence(per_frame_seq)

    logger.info(f"Built Enhanced CT with {num_frames} frames, "
                f"PixelSpacing={pixel_measures.PixelSpacing}, "
                f"SpacingBetweenSlices={getattr(pixel_measures, 'SpacingBetweenSlices', 'N/A')}")

    # Build output filename
    patient_name = str(getattr(template_ds, 'PatientName', 'unknown')).replace('^', '_')
    series_num = str(getattr(template_ds, 'SeriesNumber', '0'))
    safe_name = "".join(c for c in patient_name if c.isalnum() or c in ('_', '-'))
    output_path = os.path.join(output_folder, f"{safe_name}_series{series_num}_multiframe.dcm")

    pydicom.dcmwrite(output_path, out_ds, write_like_original=False)
    return output_path


# ============================================================
# 9. SPLIT MULTI-FRAME TO SINGLE-FRAME
# ============================================================

def split_multiframe(filepath: str, output_folder: Optional[str] = None) -> List[str]:
    """Split a multi-frame DICOM file into individual single-frame files."""
    if np is None:
        raise ImportError("numpy is required for split operation")

    ds = pydicom.dcmread(filepath)

    num_frames = getattr(ds, 'NumberOfFrames', 1)
    if isinstance(num_frames, str):
        num_frames = int(num_frames)

    if num_frames <= 1:
        logger.info(f"File {filepath} is already single-frame")
        return [filepath]

    pixel_array = get_pixel_array(ds)

    if output_folder is None:
        output_folder = os.path.dirname(filepath)

    basename = Path(filepath).stem
    results = []

    for i in range(num_frames):
        frame_ds = copy.deepcopy(ds)
        frame_ds.NumberOfFrames = 1
        frame_ds.PixelData = pixel_array[i].tobytes()
        frame_ds.InstanceNumber = i + 1
        frame_ds.SOPInstanceUID = generate_uid()
        if hasattr(frame_ds, 'file_meta'):
            frame_ds.file_meta.MediaStorageSOPInstanceUID = frame_ds.SOPInstanceUID

        out_path = os.path.join(output_folder, f"{basename}_frame{i:04d}.dcm")
        frame_ds.save_as(out_path)
        results.append(out_path)

    return results


def split_multiframe_folder(folder: str, include_subfolders: bool = True) -> List[str]:
    """Split all multi-frame DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            outs = split_multiframe(f)
            results.extend(outs)
        except Exception as e:
            logger.error(f"Failed to split {f}: {e}")
    return results


# ============================================================
# 10-11. NEMA2 <-> DICOM 3 PART 10 CONVERSION
# ============================================================

def nema2_to_dicom3(filepath: str, overwrite: bool = False) -> str:
    """Convert NEMA2 file to DICOM 3 Part 10."""
    ds = pydicom.dcmread(filepath, force=True)

    # Ensure file meta information exists
    if not hasattr(ds, 'file_meta') or ds.file_meta is None:
        ds.file_meta = pydicom.Dataset()

    if not hasattr(ds.file_meta, 'TransferSyntaxUID'):
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    if not hasattr(ds.file_meta, 'MediaStorageSOPClassUID'):
        ds.file_meta.MediaStorageSOPClassUID = getattr(ds, 'SOPClassUID', '1.2.840.10008.5.1.4.1.1.7')

    if not hasattr(ds.file_meta, 'MediaStorageSOPInstanceUID'):
        ds.file_meta.MediaStorageSOPInstanceUID = getattr(ds, 'SOPInstanceUID', generate_uid())

    output_path = get_output_path(filepath, overwrite, "_dcm3")
    ds.save_as(output_path, write_like_original=False)
    return output_path


def dicom3_to_nema2(filepath: str, overwrite: bool = False) -> str:
    """Convert DICOM 3 Part 10 file to NEMA2 (no preamble/meta)."""
    ds = pydicom.dcmread(filepath)

    output_path = get_output_path(filepath, overwrite, "_nema2")

    # Write without preamble and file meta
    with open(output_path, 'wb') as f:
        # Write dataset without file meta
        pydicom.dcmwrite(f, ds, write_like_original=False)

    return output_path


def convert_nema2_folder(folder: str, to_dicom3: bool = True,
                         include_subfolders: bool = True,
                         overwrite: bool = False) -> List[str]:
    """Convert all files in folder between NEMA2 and DICOM3."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            if to_dicom3:
                out = nema2_to_dicom3(f, overwrite)
            else:
                out = dicom3_to_nema2(f, overwrite)
            results.append(out)
            logger.info(f"Converted: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to convert {f}: {e}")
    return results


# ============================================================
# 12-13. ENDIAN CONVERSION
# ============================================================

def convert_endian(filepath: str, to_little: bool = True, overwrite: bool = False) -> str:
    """Convert between big-endian and little-endian DICOM files."""
    ds = pydicom.dcmread(filepath)

    if to_little:
        target_ts = ExplicitVRLittleEndian
        suffix = "_le"
    else:
        target_ts = ExplicitVRBigEndian
        suffix = "_be"

    ds.file_meta.TransferSyntaxUID = target_ts

    output_path = get_output_path(filepath, overwrite, suffix)
    # Use dcmwrite with explicit little/big endian flags (save_as refuses endian changes)
    pydicom.dcmwrite(output_path, ds, write_like_original=False)
    return output_path


def convert_endian_folder(folder: str, to_little: bool = True,
                          include_subfolders: bool = True,
                          overwrite: bool = False) -> List[str]:
    """Convert endianness of all DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = convert_endian(f, to_little, overwrite)
            results.append(out)
            logger.info(f"Converted endian: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to convert {f}: {e}")
    return results


# ============================================================
# 14-15. JPEG COMPRESSION (LOSSLESS / LOSSY)
# ============================================================

def compress_dicom(filepath: str, lossless: bool = True, overwrite: bool = False) -> str:
    """Compress an uncompressed DICOM file using JPEG."""
    if Image is None or np is None:
        raise ImportError("Pillow and numpy required for compression")

    ds = pydicom.dcmread(filepath)

    # Check if already compressed
    current_ts = str(ds.file_meta.TransferSyntaxUID)
    if current_ts in COMPRESSED_SYNTAXES:
        logger.info(f"File {filepath} is already compressed")
        return filepath

    pixel_array = get_pixel_array(ds)

    num_frames = getattr(ds, 'NumberOfFrames', 1)
    if isinstance(num_frames, str):
        num_frames = int(num_frames)

    if lossless:
        # Use JPEG Lossless (via JPEG2000 lossless which pydicom handles better)
        target_ts = TS_JPEG2000_LOSSLESS
        suffix = "_jll"
    else:
        # Use JPEG Lossy (baseline for 8-bit, JPEG2000 for >8-bit)
        bits = getattr(ds, 'BitsAllocated', 16)
        if bits <= 8:
            target_ts = TS_JPEG_BASELINE
        else:
            target_ts = TS_JPEG2000
        suffix = "_jl"

    # Encode frames using Pillow
    encoded_frames = []

    if num_frames > 1:
        for i in range(num_frames):
            frame = pixel_array[i]
            encoded = _encode_frame_jpeg(frame, ds, lossless)
            encoded_frames.append(encoded)
    else:
        encoded = _encode_frame_jpeg(pixel_array, ds, lossless)
        encoded_frames.append(encoded)

    # Encapsulate pixel data
    ds.PixelData = encapsulate(encoded_frames)
    ds['PixelData'].is_undefined_length = True
    ds.file_meta.TransferSyntaxUID = target_ts

    output_path = get_output_path(filepath, overwrite, suffix)
    ds.save_as(output_path)
    return output_path


def _encode_frame_jpeg(frame: 'np.ndarray', ds: Dataset, lossless: bool = True) -> bytes:
    """Encode a single frame as JPEG."""
    import io

    # Normalize to 8-bit for JPEG encoding
    if frame.dtype != np.uint8:
        frame_norm = _normalize_pixel_data(frame)
    else:
        frame_norm = frame

    img = Image.fromarray(frame_norm)

    buffer = io.BytesIO()
    if lossless:
        # PNG as intermediate for lossless (then stored as JPEG2000)
        img.save(buffer, format='JPEG2000', irreversible=False)
    else:
        img.save(buffer, format='JPEG', quality=90)

    return buffer.getvalue()


def decompress_dicom(filepath: str, overwrite: bool = False) -> str:
    """Decompress a compressed DICOM file."""
    ds = pydicom.dcmread(filepath)

    current_ts = str(ds.file_meta.TransferSyntaxUID)
    if current_ts not in COMPRESSED_SYNTAXES:
        logger.info(f"File {filepath} is already uncompressed")
        return filepath

    # Decompress pixel data
    try:
        pixel_array = ds.pixel_array
    except Exception as e:
        raise ValueError(f"Cannot decompress {filepath}: {e}")

    ds.PixelData = pixel_array.tobytes()
    ds['PixelData'].is_undefined_length = False
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    # Remove encapsulation-related tags
    if 'ExtendedOffsetTable' in ds:
        del ds['ExtendedOffsetTable']

    output_path = get_output_path(filepath, overwrite, "_unc")
    ds.save_as(output_path)
    return output_path


def compress_folder(folder: str, lossless: bool = True,
                    include_subfolders: bool = True,
                    overwrite: bool = False) -> List[str]:
    """Compress all uncompressed DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = compress_dicom(f, lossless, overwrite)
            results.append(out)
            logger.info(f"Compressed: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to compress {f}: {e}")
    return results


def decompress_folder(folder: str, include_subfolders: bool = True,
                      overwrite: bool = False) -> List[str]:
    """Decompress all compressed DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = decompress_dicom(f, overwrite)
            results.append(out)
            logger.info(f"Decompressed: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to decompress {f}: {e}")
    return results


# ============================================================
# 16. EXPORT HEADER TO TEXT
# ============================================================

def export_header(filepath: str, output_path: Optional[str] = None) -> str:
    """Export the DICOM header to a text file."""
    ds = pydicom.dcmread(filepath, stop_before_pixels=True)

    if output_path is None:
        output_path = str(Path(filepath).with_suffix('.txt'))

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"DICOM Header Export - {os.path.basename(filepath)}\n")
        f.write("=" * 80 + "\n\n")

        # File Meta Information
        if hasattr(ds, 'file_meta'):
            f.write("--- File Meta Information ---\n")
            for elem in ds.file_meta:
                f.write(f"  {elem}\n")
            f.write("\n")

        # Dataset elements
        f.write("--- Dataset ---\n")
        for elem in ds:
            if elem.tag.group == 0x7FE0:  # Skip pixel data
                f.write(f"  {elem.tag} {elem.VR} {elem.keyword}: [Pixel Data - not shown]\n")
            else:
                f.write(f"  {elem}\n")

    return output_path


def export_headers_folder(folder: str, include_subfolders: bool = True) -> List[str]:
    """Export headers of all DICOM files in a folder to text files."""
    files = find_dicom_files(folder, include_subfolders)
    results = []
    for f in files:
        try:
            out = export_header(f)
            results.append(out)
            logger.info(f"Exported header: {f} -> {out}")
        except Exception as e:
            logger.error(f"Failed to export header {f}: {e}")
    return results


# ============================================================
# SUMMARY / INFO
# ============================================================

def get_dicom_info(filepath: str) -> Dict[str, Any]:
    """Get summary information about a DICOM file."""
    ds = pydicom.dcmread(filepath, stop_before_pixels=True)

    info = {
        'filepath': filepath,
        'patient_name': str(getattr(ds, 'PatientName', 'N/A')),
        'patient_id': str(getattr(ds, 'PatientID', 'N/A')),
        'study_date': str(getattr(ds, 'StudyDate', 'N/A')),
        'modality': str(getattr(ds, 'Modality', 'N/A')),
        'rows': getattr(ds, 'Rows', 0),
        'columns': getattr(ds, 'Columns', 0),
        'bits_allocated': getattr(ds, 'BitsAllocated', 0),
        'num_frames': int(getattr(ds, 'NumberOfFrames', 1)),
        'transfer_syntax': str(ds.file_meta.TransferSyntaxUID) if hasattr(ds, 'file_meta') else 'N/A',
        'series_uid': str(getattr(ds, 'SeriesInstanceUID', 'N/A')),
        'study_uid': str(getattr(ds, 'StudyInstanceUID', 'N/A')),
        'sop_class': str(getattr(ds, 'SOPClassUID', 'N/A')),
        'photometric': str(getattr(ds, 'PhotometricInterpretation', 'N/A')),
    }

    ts = info['transfer_syntax']
    if ts in COMPRESSED_SYNTAXES:
        info['compressed'] = True
    else:
        info['compressed'] = False

    return info


def get_folder_summary(folder: str, include_subfolders: bool = True) -> Dict:
    """Get summary of DICOM files in a folder."""
    files = find_dicom_files(folder, include_subfolders)

    patients = set()
    series = set()
    modalities = set()
    total_frames = 0
    total_size = 0

    for f in files:
        try:
            info = get_dicom_info(f)
            patients.add(info['patient_id'])
            series.add(info['series_uid'])
            modalities.add(info['modality'])
            total_frames += info['num_frames']
            total_size += os.path.getsize(f)
        except Exception:
            pass

    return {
        'folder': folder,
        'total_files': len(files),
        'total_patients': len(patients),
        'total_series': len(series),
        'modalities': list(modalities),
        'total_frames': total_frames,
        'total_size_mb': round(total_size / 1024 / 1024, 2),
        'patients': list(patients)
    }
