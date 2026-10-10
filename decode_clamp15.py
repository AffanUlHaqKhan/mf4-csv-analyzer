#!/usr/bin/env python3
"""
Decode clamp15netw_BASE signal from message ID 2566871591.

Signal definition:
- Message: diagVehState_BASE (ID: 2566871591 / 0x98FEE717)
- Signal: clamp15netw_BASE
- Start bit: 12
- Length: 2 bits
- Byte order: Little Endian
- Signed: No
- Factor: 1, Offset: 0

Usage:
    python decode_clamp15.py <csv_folder>
"""

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional


# Message and signal definitions
MESSAGE_ID = 2566871591
MESSAGE_NAME = "diagVehState_BASE"
SIGNAL_NAME = "clamp15netw_BASE"
START_BIT = 12
LENGTH = 2
IS_LITTLE_ENDIAN = True
IS_SIGNED = False
FACTOR = 1
OFFSET = 0


def parse_data_bytes(data_str: str) -> bytes:
    """Parse the DataBytes string format '[b0 b1 b2 ...]' into bytes."""
    data_str = data_str.strip()
    if data_str.startswith('[') and data_str.endswith(']'):
        data_str = data_str[1:-1]
    parts = data_str.split()
    return bytes([int(p) for p in parts if p])


def extract_signal_little_endian(data: bytes, start_bit: int, length: int) -> int:
    """Extract bits in little-endian (Intel) byte order."""
    result = 0
    for i in range(length):
        bit_pos = start_bit + i
        byte_idx = bit_pos // 8
        bit_in_byte = bit_pos % 8

        if byte_idx < len(data):
            if (data[byte_idx] >> bit_in_byte) & 1:
                result |= (1 << i)

    return result


def decode_signal(data: bytes) -> float:
    """Decode the clamp15netw_BASE signal from raw data."""
    raw_value = extract_signal_little_endian(data, START_BIT, LENGTH)

    # Apply signed conversion if needed
    if IS_SIGNED and LENGTH > 0:
        max_val = 1 << (LENGTH - 1)
        if raw_value >= max_val:
            raw_value -= (1 << LENGTH)

    # Apply factor and offset
    return raw_value * FACTOR + OFFSET


def detect_column_names(headers: List[str]) -> Optional[Dict[str, str]]:
    """Detect the column naming pattern in CSV headers."""
    id_col = None
    data_col = None
    bus_col = None

    for header in headers:
        header_lower = header.lower()
        if header_lower.endswith('.id') and 'error' not in header_lower:
            id_col = header
        elif header_lower.endswith('.databytes') and 'error' not in header_lower:
            data_col = header
        elif header_lower.endswith('.buschannel') and 'error' not in header_lower:
            bus_col = header

    if id_col and data_col and bus_col:
        return {'id': id_col, 'databytes': data_col, 'buschannel': bus_col}
    return None


def process_csv_files(csv_folder: str):
    """Process CSV files and decode clamp15netw_BASE signal."""
    csv_files = glob.glob(os.path.join(csv_folder, '*.csv'))

    if not csv_files:
        print(f"No CSV files found in {csv_folder}")
        return

    print(f"Found {len(csv_files)} CSV files")
    print(f"\nLooking for message ID: {MESSAGE_ID} (0x{MESSAGE_ID:08X})")
    print(f"Signal: {SIGNAL_NAME}")
    print(f"  Start bit: {START_BIT}, Length: {LENGTH} bits")
    print(f"  Byte order: {'Little Endian' if IS_LITTLE_ENDIAN else 'Big Endian'}")

    # Collect all frames for the target message
    all_frames = []

    for csv_file in sorted(csv_files):
        filename = os.path.basename(csv_file)

        with open(csv_file, 'r', newline='') as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                continue

            col_map = detect_column_names(reader.fieldnames)
            if col_map is None:
                continue

            for row in reader:
                try:
                    msg_id = int(float(row[col_map['id']]))

                    if msg_id == MESSAGE_ID:
                        timestamp = float(row['timestamps'])
                        data_bytes_str = row[col_map['databytes']]
                        bus_channel = int(float(row[col_map['buschannel']]))

                        all_frames.append({
                            'timestamp': timestamp,
                            'bus_channel': bus_channel,
                            'data_bytes_str': data_bytes_str,
                            'source_file': filename
                        })
                except Exception as e:
                    continue

    print(f"\nFound {len(all_frames)} frames for message {MESSAGE_ID}")

    if not all_frames:
        print("No frames found for this message ID!")
        return

    # Sort by timestamp
    all_frames.sort(key=lambda x: x['timestamp'])

    # Decode signal and track changes
    decoded_values = []
    changes = []
    last_value = None

    for frame in all_frames:
        data = parse_data_bytes(frame['data_bytes_str'])
        value = decode_signal(data)

        decoded_values.append({
            'timestamp': frame['timestamp'],
            'bus_channel': frame['bus_channel'],
            'raw_data': data.hex(),
            'raw_data_bytes': list(data),
            SIGNAL_NAME: value
        })

        # Track changes
        if last_value is None:
            changes.append({'value': value, 'timestamp': frame['timestamp']})
            last_value = value
        elif value != last_value:
            changes.append({'value': value, 'timestamp': frame['timestamp']})
            last_value = value

    # Calculate change count
    change_count = len(changes) - 1 if len(changes) > 0 else 0

    # Print summary
    print(f"\n{'='*60}")
    print("DECODING RESULTS")
    print(f"{'='*60}")
    print(f"Total frames decoded: {len(decoded_values)}")
    print(f"Value changes: {change_count}")
    print(f"\nUnique values found: {set(d[SIGNAL_NAME] for d in decoded_values)}")

    # Show first 10 decoded values
    print(f"\nFirst 10 decoded frames:")
    print(f"{'Timestamp':<20} {'Raw Bytes':<30} {SIGNAL_NAME}")
    print("-" * 60)
    for entry in decoded_values[:10]:
        print(f"{entry['timestamp']:<20.6f} {entry['raw_data_bytes']} {entry[SIGNAL_NAME]}")

    # Show all changes
    print(f"\nAll value changes ({len(changes)} entries, {change_count} transitions):")
    for i, change in enumerate(changes[:50]):  # Show first 50 changes
        print(f"  [{i}] t={change['timestamp']:.6f}s -> value={change['value']}")
    if len(changes) > 50:
        print(f"  ... and {len(changes) - 50} more changes")

    # Export full results
    output = {
        "message_id": MESSAGE_ID,
        "message_id_hex": f"0x{MESSAGE_ID:08X}",
        "message_name": MESSAGE_NAME,
        "signal_name": SIGNAL_NAME,
        "signal_definition": {
            "start_bit": START_BIT,
            "length": LENGTH,
            "byte_order": "LittleEndian",
            "is_signed": IS_SIGNED,
            "factor": FACTOR,
            "offset": OFFSET
        },
        "total_frames": len(decoded_values),
        "change_count": change_count,
        "unique_values": list(set(d[SIGNAL_NAME] for d in decoded_values)),
        "changes": changes,
        "all_decoded_values": decoded_values
    }

    output_file = os.path.join(csv_folder, '..', 'clamp15_decoded.json')
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    # Export ONLY the onchange data to a separate file
    onchange_output = {
        "signal_name": SIGNAL_NAME,
        "message_id": MESSAGE_ID,
        "message_id_hex": f"0x{MESSAGE_ID:08X}",
        "message_name": MESSAGE_NAME,
        "total_frames": len(decoded_values),
        "change_count": change_count,
        "unique_values": list(set(d[SIGNAL_NAME] for d in decoded_values)),
        "changes": changes
    }

    onchange_file = os.path.join(csv_folder, '..', 'clamp15_onchange.json')
    with open(onchange_file, 'w') as f:
        json.dump(onchange_output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Full results exported to: {output_file}")
    print(f"OnChange results exported to: {onchange_file}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description='Decode clamp15netw_BASE signal from CSV files'
    )
    parser.add_argument('csv_folder', help='Path to folder containing CSV files')

    args = parser.parse_args()

    if not os.path.isdir(args.csv_folder):
        print(f"Error: CSV folder not found: {args.csv_folder}")
        sys.exit(1)

    process_csv_files(args.csv_folder)


if __name__ == '__main__':
    main()
