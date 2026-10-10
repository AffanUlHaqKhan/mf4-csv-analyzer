# CAN Signal Decoder

Python tools for parsing CAN bus data and decoding signal values using a DBC (CAN database) file. Supports both CSV files and MF4 (ASAM MDF4) files. Includes signal change tracking to monitor when values change over time.

## Tools

| Script | Input | Description |
|--------|-------|-------------|
| `can_decoder.py` | CSV files | Decode CAN signals from CSV files exported from MF4 |
| `mf4_decoder.py` | MF4 file | Decode CAN signals directly from MF4 files (1GB+ supported) |

## Requirements

- Python 3.7+

### For CSV Decoder (`can_decoder.py`)
- No external dependencies (uses only standard library)

### For MF4 Decoder (`mf4_decoder.py`)
- `asammdf` library

## Installation

```bash
git clone <repository-url>
cd mf4-csv-analyzer

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies for MF4 support
pip install -r requirements.txt
```

---

## CSV Decoder (`can_decoder.py`)

### Usage

```bash
# Basic usage (console output only)
python can_decoder.py <dbc_file> <csv_folder>

# Export decoded data to JSON
python can_decoder.py <dbc_file> <csv_folder> --output decoded_output.json

# Export signal changes to JSON
python can_decoder.py <dbc_file> <csv_folder> --changes signal_changes.json

# Quiet mode (suppress verbose DBC listing)
python can_decoder.py <dbc_file> <csv_folder> -q

# Full example (all outputs)
python can_decoder.py vehicle.dbc csv_folder/ -o results.json -c changes.json -q

# Use 32-bit ID format (with extended bit) to match DBC file format
python can_decoder.py vehicle.dbc csv_folder/ --id-format 32bit -o results.json
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `dbc_file` | Path to the DBC file containing message/signal definitions |
| `csv_folder` | Path to folder containing CSV files with CAN data |
| `-o, --output` | Output file for decoded data (CSV or JSON based on extension) |
| `-c, --changes` | Output file for signal changes (JSON) |
| `-q, --quiet` | Suppress verbose DBC message listing |
| `--id-format` | ID format for output: `29bit` (raw CAN ID, default) or `32bit` (with extended bit 0x80000000) |

### Input CSV Format

The tool expects CSV files with the following columns:

| Column | Description |
|--------|-------------|
| `timestamps` | Message timestamp in seconds |
| `*.ID` | CAN message ID (decimal) - auto-detected column ending in `.ID` |
| `*.DataBytes` | Raw bytes in format `[b0 b1 b2 ...]` - auto-detected |
| `*.BusChannel` | CAN bus channel number - auto-detected |

The tool automatically detects column names matching patterns like:
- `CAN_DataFrame.CAN_DataFrame.ID`
- `CAN_DataFrame.CAN_DataFrame.DataBytes`
- `CAN_DataFrame.CAN_DataFrame.BusChannel`

Files with `error` in the column names (e.g., `CAN_ErrorFrame`) are automatically skipped.

---

## MF4 Decoder (`mf4_decoder.py`)

Directly reads MF4 (ASAM MDF4) measurement files without needing to convert to CSV first. Optimized for large files (1GB+).

### Usage

```bash
# Basic usage (console output only)
python mf4_decoder.py <dbc_file> <mf4_file>

# Export decoded data to JSON
python mf4_decoder.py <dbc_file> <mf4_file> --output decoded_output.json

# Export signal changes to JSON
python mf4_decoder.py <dbc_file> <mf4_file> --changes signal_changes.json

# Quiet mode
python mf4_decoder.py <dbc_file> <mf4_file> -q

# Full example (all outputs)
python mf4_decoder.py vehicle.dbc recording.mf4 -o results.json -c changes.json -q

# Use 32-bit ID format (with extended bit) to match DBC file format
python mf4_decoder.py vehicle.dbc recording.mf4 --id-format 32bit -o results.json
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `dbc_file` | Path to the DBC file containing message/signal definitions |
| `mf4_file` | Path to the MF4 file |
| `-o, --output` | Output file for decoded data (CSV or JSON based on extension) |
| `-c, --changes` | Output file for signal changes (JSON) |
| `-q, --quiet` | Suppress verbose DBC message listing |
| `--id-format` | ID format for output: `29bit` (raw CAN ID, default) or `32bit` (with extended bit 0x80000000) |

### Large File Handling

The MF4 decoder is optimized for large files:
- Uses `memory='minimum'` mode to reduce RAM usage
- Processes data by channel groups
- Shows progress every 100,000 frames
- Tested with 1GB+ files

---

## Output

### Console Output

Both tools display:
- Number of messages and signals loaded from DBC (including multiplexed signals)
- Number of files/groups processed
- Summary of matched/unmatched message IDs
- Analysis of unmatched IDs (standard vs extended/J1939)
- Decoded message types with frame counts
- Signal change statistics (top signals by change count)
- Sample decoded frames with signal values

### Unique Message IDs (`unique_message_ids.json`)

Automatically exported to the same directory as the input:

```json
[
  {
    "message_id": 2566871591,
    "message_id_hex": "0x98FF5A27",
    "message_name": "NOT_IN_DBC",
    "frame_count": 17000,
    "in_dbc": false,
    "is_extended_id": true,
    "j1939": {
      "pgn": 65370,
      "pgn_hex": "0xFF5A",
      "source_address": 39,
      "priority": 6
    }
  }
]
```

### CSV Output (`--output *.csv`)

Flattened format with columns:
- `timestamp`
- `bus_channel`
- `message_id_hex`
- `message_name`
- `raw_data`
- One column per signal name

### JSON Output (`--output *.json`)

Structured format with full signal metadata including units.

### Signal Changes Output (`--changes`)

JSON file with change tracking for each signal:

```json
[
  {
    "signal_name": "VehicleSpeed",
    "change_count": 7,
    "changes": [
      {"value": 0.0, "timestamp": 100.0},
      {"value": 10.5, "timestamp": 100.5},
      {"value": 15.0, "timestamp": 101.2}
    ]
  }
]
```

**Change count explanation:** For a signal with values like `1,1,0,0,0,1,0`, the recorded changes would be `[1, 0, 1, 0]` (4 entries), and the `change_count` would be `3` (number of transitions/shifts between different values).

---

## Features

- **DBC Parsing**
  - Parses standard DBC file format
  - Supports multiplexed signals (M, m0, m1, etc.)
  - Handles both standard (11-bit) and extended (29-bit) CAN IDs
  - Automatic ID format conversion between DBC (with 0x80000000 extended bit) and MF4/CSV (raw 29-bit)
  - User-selectable output ID format: `29bit` (raw CAN ID) or `32bit` (with extended bit)

- **Signal Decoding**
  - Supports little-endian (Intel) and big-endian (Motorola) byte ordering
  - Handles signed and unsigned signal values
  - Applies scale and offset to raw values

- **Data Processing**
  - CSV: Processes multiple CSV files in batch, auto-detects column names
  - MF4: Reads directly from MF4 files, optimized for large files
  - Sorts all frames by timestamp before processing

- **Signal Change Tracking**
  - Records every value change with timestamp
  - Reports how many times each signal value changed
  - Identifies signals that never changed

- **J1939 Support**
  - Detects extended (29-bit) CAN IDs
  - Extracts J1939 PGN, source address, and priority
  - Reports when unmatched IDs are likely J1939 format

---

## Troubleshooting

### CSV Decoder

**"No CSV files found"**
- Verify the CSV folder path exists and contains `.csv` files

**"Skipping: Not a CAN DataFrame file"**
- This is normal - the tool skips error frame files and other non-CAN-data CSVs
- Only files with `.ID`, `.DataBytes`, and `.BusChannel` columns are processed

### MF4 Decoder

**"asammdf library is required"**
- Install with: `pip install asammdf`
- Or use the virtual environment: `source venv/bin/activate`

**Memory issues with large files**
- The decoder uses `memory='minimum'` mode by default
- If still having issues, try processing a smaller time range of data

### Both Tools

**"Loaded 0 messages from DBC file"**
- Check that the DBC file is valid and properly formatted
- Ensure message definitions start with `BO_` and signal definitions with `SG_`

**"Messages not in DBC" is high**
- The message IDs in the data must match those defined in the DBC file
- If most unmatched IDs are extended (J1939), you need a J1939 DBC file
- Check the `unique_message_ids.json` output to see which IDs are present

---

## Example Output

```
Loading DBC file: vehicle.dbc
Loaded 159 messages with 2752 signals from DBC file
  (includes 1349 multiplexed signals)

Opening MF4 file: recording.mf4
File size: 1024.5 MB
MF4 file contains 36 channel groups
Processing group 0: CAN_DataFrame
  Found 2463020 frames
  Processed 100000 frames...
  Processed 200000 frames...
...

============================================================
DECODING SUMMARY
============================================================
Total unique message IDs in MF4 file: 1082
Messages matched in DBC: 45
Messages not in DBC: 1037
Total decoded frames: 156000

Unmatched message ID analysis:
  Standard CAN IDs (11-bit, <= 0x7FF): 11
  Extended CAN IDs (29-bit, J1939): 1026

============================================================
SIGNAL CHANGE STATISTICS
============================================================
Total unique signals tracked: 523

Top 20 signals by change count:
Signal Name                                           Changes
--------------------------------------------------------------
VehicleSpeed                                            15420
EngineRPM                                               12350
ThrottlePosition                                         8920
...
```
