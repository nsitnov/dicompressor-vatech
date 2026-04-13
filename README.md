# DicomPressor Vatech

Dedicated DICOM merge workflow for **Vatech 3D studies**.

This repository is the Vatech-specific variant of DicomPressor. It is meant for exports that contain `DCM_FILE.CT` archives instead of a folder with hundreds of small single-frame DICOM files.

The script supports both patterns in the same folder tree:

- normal folders with many single-frame `.dcm` slices;
- Vatech archives like `DCM_FILE.CT`;
- sample-compatible files like `DCM_FILE.CT.dcm`.

For every Vatech archive it:

1. extracts the ZIP contents to a temporary folder;
2. merges the extracted slices into one multi-frame DICOM;
3. moves the merged result back next to the source data;
4. optionally copies the merged result to `--output-dir`;
5. deletes the temporary folder;
6. writes `.dicompressor_vatech_done` so the same folder is not processed again.

The original generic workflow for other CT / CBCT machines stays in the main DicomPressor repository.

## Requirements

- Python 3.8+
- pydicom
- numpy
- Pillow

## Quick Install

### macOS / Linux / WSL

```bash
chmod +x dicompressor-vatech.sh
./dicompressor-vatech.sh -j -F /path/to/patient_folder
```

### Windows PowerShell

```powershell
.\dicompressor-vatech.ps1 -j -F "C:\path\to\patient_folder"
```

### Direct Python

```bash
pip install -r requirements.txt
python3 dicompressor-vatech.py -j -F /path/to/patient_folder
```

## Common Commands

```bash
# Process one folder
python3 dicompressor-vatech.py -j -F /path/to/patient_folder

# Recursively scan a parent folder
python3 dicompressor-vatech.py -j --skip-if-done -f /path/to/patients

# Watch mode
python3 dicompressor-vatech.py -j --watch 300 -f /path/to/patients

# Watch + output dir
python3 dicompressor-vatech.py -j --watch 300 --output-dir /data/merged -f /data/patients
```

## Included Files

- `dicompressor-vatech.py` - dedicated Vatech CLI
- `dicompressor-vatech.sh` - Bash wrapper
- `dicompressor-vatech.ps1` - PowerShell wrapper
- `dicompressor-vatech-watch.sh` - Linux/macOS/WSL watch script
- `dicompressor-vatech-watch.ps1` - Windows watch script
- `dicom_utils.py` - shared DICOM merge core
- `requirements.txt` - Python dependencies

## Marker File

After a successful run the script writes:

```text
.dicompressor_vatech_done
```

Delete that marker if you need to force a re-run for the same folder.
