#!/usr/bin/env python3
"""
CSV CAN Analyzer - Analyzes CSV files to extract unique message IDs and frame counts.

Usage:
    python csv_analyzer.py <csv_folder>
"""

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional


def detect_column_names(headers: List[str]) -> Optional[Dict[str, str]]:
    """
    Detect the column naming pattern in CSV headers.
    Returns a dict mapping standard names to actual column names, or None if not a CAN data file.
    """
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
        return {
            'id': id_col,
            'databytes': data_col,
            'buschannel': bus_col
        }

    return None


def analyze_csv_files(csv_folder: str):
    """Analyze all CSV files in the folder and extract unique message IDs."""
    csv_files = glob.glob(os.path.join(csv_folder, '*.csv'))

    if not csv_files:
        print(f"No CSV files found in {csv_folder}")
        return

    print(f"Found {len(csv_files)} CSV files to process")

    # Track message IDs and frame counts
    message_frame_counts: Dict[int, int] = defaultdict(int)
    message_bus_channels: Dict[int, set] = defaultdict(set)
    skipped_files = 0
    total_rows = 0

    for csv_file in sorted(csv_files):
        filename = os.path.basename(csv_file)
        print(f"Processing: {filename}")

        with open(csv_file, 'r', newline='') as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                print(f"  Skipping: No headers found")
                skipped_files += 1
                continue

            col_map = detect_column_names(reader.fieldnames)
            if col_map is None:
                print(f"  Skipping: Not a CAN DataFrame file")
                skipped_files += 1
                continue

            file_rows = 0
            for row in reader:
                try:
                    msg_id = int(float(row[col_map['id']]))
                    bus_channel = int(float(row[col_map['buschannel']]))

                    message_frame_counts[msg_id] += 1
                    message_bus_channels[msg_id].add(bus_channel)
                    file_rows += 1
                except Exception as e:
                    continue

            print(f"  Rows processed: {file_rows}")
            total_rows += file_rows

    if skipped_files > 0:
        print(f"\nSkipped {skipped_files} non-CAN-data files")

    # Print summary
    print(f"\n{'='*70}")
    print("ANALYSIS SUMMARY")
    print(f"{'='*70}")
    print(f"Total rows processed: {total_rows}")
    print(f"Unique message IDs found: {len(message_frame_counts)}")

    # Print all unique message IDs
    print(f"\n{'Message ID':<15} {'Hex':<14} {'Frames':>10} {'Bus Channels'}")
    print("-" * 70)

    for msg_id in sorted(message_frame_counts.keys()):
        frame_count = message_frame_counts[msg_id]
        bus_channels = sorted(message_bus_channels[msg_id])
        bus_str = ', '.join(str(b) for b in bus_channels)
        print(f"{msg_id:<15} 0x{msg_id:08X}   {frame_count:>10} {bus_str}")

    # Export to JSON
    output_file = os.path.join(csv_folder, '..', 'unique_message_ids.json')
    output_data = []
    for msg_id in sorted(message_frame_counts.keys()):
        output_data.append({
            "message_id": msg_id,
            "message_id_hex": f"0x{msg_id:08X}",
            "frame_count": message_frame_counts[msg_id],
            "bus_channels": sorted(message_bus_channels[msg_id])
        })

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n{'='*70}")
    print(f"Results exported to: {output_file}")
    print(f"{'='*70}")

    return output_data


def main():
    parser = argparse.ArgumentParser(
        description='Analyze CSV files to extract unique CAN message IDs and frame counts'
    )
    parser.add_argument('csv_folder', help='Path to folder containing CSV files')

    args = parser.parse_args()

    if not os.path.isdir(args.csv_folder):
        print(f"Error: CSV folder not found: {args.csv_folder}")
        sys.exit(1)

    analyze_csv_files(args.csv_folder)


if __name__ == '__main__':
    main()
