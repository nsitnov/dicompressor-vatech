# DicomPressor Vatech

Dedicated DICOM merge workflow for **Vatech 3D studies**.

GitHub repository: `https://github.com/nsitnov/dicompressor-vatech`

This variant is for Vatech exports that contain `DCM_FILE.CT` archives instead of a folder with hundreds of small single-frame DICOM files. Each `.CT` archive is treated as a ZIP file, extracted to a temporary folder, merged into one multi-frame DICOM, copied to the output folder if requested, and then cleaned up. A `.dicompressor_vatech_done` marker is written so the same folder is not processed again.

The original `dicompressor.py` remains the generic workflow for other CBCT / CT machines that already export many `.dcm` slices in a folder.

## Requirements

- Python 3.8+
- pydicom
- numpy
- Pillow

## Quick Install

```bash
pip install pydicom numpy Pillow
```

If you downloaded the ZIP package, unzip it first and run the commands from that folder.

## Usage

### macOS / Linux / WSL

```bash
git clone https://github.com/nsitnov/dicompressor-vatech.git
cd dicompressor-vatech
./dicompressor-vatech.sh -j -F /path/to/patient_folder

# or directly
python3 dicompressor-vatech.py -j -F /path/to/patient_folder
```

### Windows PowerShell

```powershell
git clone https://github.com/nsitnov/dicompressor-vatech.git
cd dicompressor-vatech
python -m pip install -r .\requirements.txt
python .\dicompressor-vatech.py -j -F "C:\path\to\patient_folder"

# if "python" is not found, use:
py -3 -m pip install -r .\requirements.txt
py -3 .\dicompressor-vatech.py -j -F "C:\path\to\patient_folder"
```

### Recommended Windows Watch Mode

This is the recommended command when Vatech stores all patients under one parent folder and each patient has its own `Sub...` directory:

```powershell
python .\dicompressor-vatech.py -j --watch 300 --output-dir "D:\Vatech\Merged" -f "D:\VatechDatabase\FMData\Files"
```

That command:

- scans every 300 seconds
- looks recursively under `D:\VatechDatabase\FMData\Files`
- finds Vatech `DCM_FILE.CT` archives inside patient subfolders
- merges them to multi-frame DICOM
- copies the merged result to `D:\Vatech\Merged`
- writes `.dicompressor_vatech_done` in each processed study folder

### Optional Windows PowerShell Wrapper

If you want to use the wrapper script instead of calling Python directly:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
Unblock-File .\dicompressor-vatech.ps1
.\dicompressor-vatech.ps1 -j -F "C:\path\to\patient_folder"
```

The direct `python .\dicompressor-vatech.py ...` command is still the safest Windows option when troubleshooting.

## Expected Vatech Folder Layout

Typical real-world Vatech structure:

```text
D:\VatechDatabase\FMData\Files\
  Sub022093\
    PX20220930_150741_0422_75551531.dcm
    CT20220930_152004_9476_56703857\
      DCM_FILE.CT
    CT20220930_154913_7937_88371364\
      DCM_FILE.CT
```

The script is designed for that layout:

- each patient stays in its own `Sub...` folder
- each 3D study lives in a nested `CT...` folder
- patient-root 2D files like `PX*.dcm` are ignored unless they form a large real slice series
- the real 3D source is the `DCM_FILE.CT` archive inside the `CT...` folder

## What It Detects

- Normal folders with many single-frame `.dcm` slices
- Vatech archive files like `DCM_FILE.CT`
- Sample-compatible files like `DCM_FILE.CT.dcm`

## Core Flags

| Flag | Description |
|------|-------------|
| `-j` | Run the Vatech merge workflow |
| `-f PATH` | Recursively scan all subfolders |
| `-F PATH` | Process only the selected folder |
| `--skip-if-done` | Skip folders that already contain `.dicompressor_vatech_done` |
| `--watch N` | Re-scan every N seconds and process only new folders |
| `--output-dir DIR` | Copy merged results to `DIR` |
| `--verbose` | Debug logging |
| `--quiet` | Warnings/errors only |

## Examples

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

## Watch Scripts

### Linux / macOS / WSL

```bash
./dicompressor-vatech-watch.sh /path/to/patients 300 /data/merged
```

### Windows PowerShell

```powershell
.\dicompressor-vatech-watch.ps1 -WatchDir "D:\DICOM\Patients" -IntervalSeconds 300 -OutputDir "D:\Merged"
```

## Output Filenames

Merged filenames do not come from the literal archive filename `DCM_FILE.CT`. They are derived from the DICOM metadata inside the archive, usually `PatientName` and `SeriesNumber`.

Examples:

```text
Stamenov_Enco_series31_multiframe.dcm
test_test_series31_multiframe.dcm
```

If the output directory already contains the same name, the script keeps both files by adding a suffix such as `_1`, `_2`, and so on.

## Common Windows Problems

### PowerShell says scripts are disabled

Error example:

```text
File ... cannot be loaded because running scripts is disabled on this system.
```

Fix:

- use the direct Python command instead of the `.ps1` wrapper
- or allow scripts only for the current terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### `python` is not recognized

Try:

```powershell
py -3 -m pip install -r .\requirements.txt
py -3 .\dicompressor-vatech.py -j --watch 300 --output-dir "D:\Vatech\Merged" -f "D:\VatechDatabase\FMData\Files"
```

If neither `python` nor `py` exists, install Python 3 and make sure it is added to `PATH`.

### `Expected implicit VR, but found explicit VR`

This message is usually a warning from `pydicom`, not a fatal error. In most cases the files are still read correctly and the merge continues.

### Nothing appears immediately in the output folder

On the first `--watch` pass, the script may be walking many old patient folders before it reaches the newest studies. Give the first scan time to finish, especially on large Vatech databases.

## Marker File

After a successful run, the script writes:

```text
.dicompressor_vatech_done
```

Delete that marker if you need to force a re-run for the same folder.
