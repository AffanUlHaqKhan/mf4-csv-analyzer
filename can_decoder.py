#!/usr/bin/env python3
"""
CAN Signal Decoder - Parses CSV files with raw CAN data and decodes signals using a DBC file.

Usage:
    python can_decoder.py <dbc_file> <csv_folder> [--output <output_file>] [--changes <changes_file>]
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Signal:
    """Represents a CAN signal definition from DBC."""
    name: str
    start_bit: int
    length: int
    byte_order: str  # 'little_endian' or 'big_endian'
    is_signed: bool
    scale: float
    offset: float
    min_val: float
    max_val: float
    unit: str


@dataclass
class Message:
    """Represents a CAN message definition from DBC."""
    id: int
    name: str
    dlc: int
    signals: List[Signal]
    is_extended: bool = False


class DBCParser:
    """Parser for DBC (CAN database) files."""

    def __init__(self, dbc_path: str, verbose: bool = True):
        self.messages: Dict[int, Message] = {}
        self.messages_by_name: Dict[str, Message] = {}
        self.verbose = verbose
        self._parse(dbc_path)

    def _parse(self, dbc_path: str):
        """Parse the DBC file and extract message/signal definitions."""
        with open(dbc_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Regex for message definition: BO_ <CAN-ID> <MessageName>: <MessageLength> <SendingNode>
        msg_pattern = re.compile(
            r'BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)'
        )

        # Regex for signal definition - handles both normal and multiplexed signals
        # Normal: SG_ <SignalName> : <StartBit>|<Length>@<ByteOrder><ValueType> ...
        # Multiplexed: SG_ <SignalName> <MuxIndicator> : <StartBit>|<Length>@<ByteOrder><ValueType> ...
        # MuxIndicator can be: M (multiplexer), m0, m1, m2, etc. (multiplexed values)
        sig_pattern = re.compile(
            r'^\s*SG_\s+(\w+)\s*(?:(M|m\d+)\s+)?:\s*(\d+)\|(\d+)@([01])([+-])\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)\s*\[\s*([^|]*)\|([^\]]*)\s*\]\s*"([^"]*)"\s*(.*)$'
        )

        current_message = None
        total_signals_parsed = 0
        multiplexed_signals = 0

        for line in content.split('\n'):
            line_stripped = line.strip()

            # Check for message definition
            msg_match = msg_pattern.match(line_stripped)
            if msg_match:
                msg_id_raw = int(msg_match.group(1))
                msg_name = msg_match.group(2)
                msg_dlc = int(msg_match.group(3))

                # Check if extended ID (bit 31 set in DBC format)
                is_extended = (msg_id_raw & 0x80000000) != 0
                if is_extended:
                    msg_id = msg_id_raw & 0x1FFFFFFF  # Mask to get 29-bit ID
                else:
                    msg_id = msg_id_raw

                current_message = Message(
                    id=msg_id,
                    name=msg_name,
                    dlc=msg_dlc,
                    signals=[],
                    is_extended=is_extended
                )
                # Store with the 29-bit ID
                self.messages[msg_id] = current_message
                # Also store with the raw DBC ID (with extended bit) for reverse lookups
                if is_extended:
                    self.messages[msg_id_raw] = current_message
                self.messages_by_name[msg_name] = current_message
                continue

            # Check for signal definition
            sig_match = sig_pattern.match(line)
            if sig_match and current_message:
                sig_name = sig_match.group(1)
                mux_indicator = sig_match.group(2)  # M, m0, m1, etc. or None
                start_bit = int(sig_match.group(3))
                length = int(sig_match.group(4))
                byte_order = 'big_endian' if sig_match.group(5) == '0' else 'little_endian'
                is_signed = sig_match.group(6) == '-'

                try:
                    scale = float(sig_match.group(7))
                except ValueError:
                    scale = 1.0

                try:
                    offset = float(sig_match.group(8))
                except ValueError:
                    offset = 0.0

                min_str = sig_match.group(9).strip()
                max_str = sig_match.group(10).strip()

                try:
                    min_val = float(min_str) if min_str else 0
                except ValueError:
                    min_val = 0

                try:
                    max_val = float(max_str) if max_str else 0
                except ValueError:
                    max_val = 0

                unit = sig_match.group(11)

                signal = Signal(
                    name=sig_name,
                    start_bit=start_bit,
                    length=length,
                    byte_order=byte_order,
                    is_signed=is_signed,
                    scale=scale,
                    offset=offset,
                    min_val=min_val,
                    max_val=max_val,
                    unit=unit
                )
                current_message.signals.append(signal)
                total_signals_parsed += 1

                if mux_indicator:
                    multiplexed_signals += 1

        print(f"Loaded {len(self.messages)} messages with {total_signals_parsed} signals from DBC file")
        if multiplexed_signals > 0:
            print(f"  (includes {multiplexed_signals} multiplexed signals)")

        # Print all message IDs from DBC for debugging (only if verbose)
        if self.verbose:
            print("\nDBC Message IDs:")
            for msg_id, msg in sorted(self.messages.items()):
                print(f"  {msg_id} (0x{msg_id:08X}) - {msg.name} - {len(msg.signals)} signals")

    def get_message(self, msg_id: int) -> Optional[Message]:
        """Get message definition by ID."""
        return self.messages.get(msg_id)

    def get_message_by_name(self, name: str) -> Optional[Message]:
        """Get message definition by name."""
        return self.messages_by_name.get(name)


class CANDecoder:
    """Decodes raw CAN data bytes into signal values."""

    @staticmethod
    def extract_signal(data: bytes, signal: Signal) -> float:
        """Extract and decode a signal value from raw CAN data bytes."""
        if len(data) == 0:
            return 0.0

        if signal.byte_order == 'little_endian':
            raw_value = CANDecoder._extract_little_endian(data, signal.start_bit, signal.length)
        else:
            raw_value = CANDecoder._extract_big_endian(data, signal.start_bit, signal.length)

        # Handle signed values
        if signal.is_signed and signal.length > 0:
            max_val = 1 << (signal.length - 1)
            if raw_value >= max_val:
                raw_value -= (1 << signal.length)

        # Apply scale and offset
        return raw_value * signal.scale + signal.offset

    @staticmethod
    def _extract_little_endian(data: bytes, start_bit: int, length: int) -> int:
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

    @staticmethod
    def _extract_big_endian(data: bytes, start_bit: int, length: int) -> int:
        """Extract bits in big-endian (Motorola) byte order."""
        result = 0

        # In Motorola format, start_bit is MSB position
        start_byte = start_bit // 8
        start_bit_in_byte = start_bit % 8

        bits_read = 0
        current_byte = start_byte
        current_bit = start_bit_in_byte

        while bits_read < length:
            if current_byte < len(data):
                if (data[current_byte] >> current_bit) & 1:
                    result |= (1 << (length - 1 - bits_read))

            bits_read += 1
            current_bit -= 1

            if current_bit < 0:
                current_byte += 1
                current_bit = 7

        return result


class SignalChangeTracker:
    """Tracks value changes for all signals."""

    def __init__(self):
        # Structure: {signal_name: {"changes": [{"value": v, "timestamp": t}, ...], "change_count": n}}
        self.signal_data: Dict[str, Dict] = defaultdict(lambda: {"changes": [], "last_value": None})

    def update(self, signal_name: str, value: float, timestamp: float):
        """Update signal value and track if it changed."""
        data = self.signal_data[signal_name]

        if data["last_value"] is None:
            # First value - record it
            data["changes"].append({"value": value, "timestamp": timestamp})
            data["last_value"] = value
        elif data["last_value"] != value:
            # Value changed - record the new value
            data["changes"].append({"value": value, "timestamp": timestamp})
            data["last_value"] = value

    def get_statistics(self) -> List[Dict]:
        """
        Get change statistics for all signals.
        Returns list of: {"signal_name": str, "change_count": int, "changes": [{"value": v, "timestamp": t}, ...]}
        """
        result = []
        for signal_name, data in sorted(self.signal_data.items()):
            changes = data["changes"]
            # Change count is number of transitions (shifts between values)
            # For sequence like 1,1,0,0,0,1,0 -> changes recorded: 1,0,1,0 -> 3 transitions
            change_count = len(changes) - 1 if len(changes) > 0 else 0

            result.append({
                "signal_name": signal_name,
                "change_count": change_count,
                "total_samples": len(changes),
                "changes": changes
            })

        return result

    def print_summary(self, top_n: int = 20):
        """Print summary of signal changes."""
        stats = self.get_statistics()

        # Sort by change count descending
        stats_sorted = sorted(stats, key=lambda x: x["change_count"], reverse=True)

        print(f"\n{'='*60}")
        print("SIGNAL CHANGE STATISTICS")
        print(f"{'='*60}")
        print(f"Total unique signals tracked: {len(stats)}")
        print(f"\nTop {top_n} signals by change count:")
        print(f"{'Signal Name':<50} {'Changes':>10}")
        print("-" * 62)

        for stat in stats_sorted[:top_n]:
            print(f"{stat['signal_name']:<50} {stat['change_count']:>10}")

        # Show signals that never changed
        unchanged = [s for s in stats if s["change_count"] == 0]
        if unchanged:
            print(f"\nSignals that never changed: {len(unchanged)}")

    def export_to_json(self, output_file: str):
        """Export change data to JSON file."""
        stats = self.get_statistics()

        # Format as requested: [{"signal_name": "", "changes": [{"value": , "timestamp": }]}]
        output = []
        for stat in stats:
            output.append({
                "signal_name": stat["signal_name"],
                "change_count": stat["change_count"],
                "changes": stat["changes"]
            })

        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"\nSignal changes exported to {output_file}")


def parse_data_bytes(data_str: str) -> bytes:
    """Parse the DataBytes string format '[b0 b1 b2 ...]' into bytes."""
    # Remove brackets and split by whitespace
    data_str = data_str.strip()
    if data_str.startswith('[') and data_str.endswith(']'):
        data_str = data_str[1:-1]

    parts = data_str.split()
    return bytes([int(p) for p in parts if p])


def format_message_id(msg_id: int, is_extended: bool, use_extended_format: bool) -> Tuple[int, str]:
    """Format message ID based on user preference.

    Args:
        msg_id: The raw message ID from the data
        is_extended: Whether this is an extended (29-bit) CAN ID
        use_extended_format: User preference for 32-bit format with extended bit

    Returns:
        Tuple of (formatted_id, hex_string)
    """
    if is_extended and use_extended_format:
        # Add the extended bit (bit 31) for 32-bit format
        formatted_id = msg_id | 0x80000000
    else:
        # Use raw 29-bit format (strip extended bit if present)
        formatted_id = msg_id & 0x1FFFFFFF

    return formatted_id, f"0x{formatted_id:08X}"


def detect_column_names(headers: List[str]) -> Optional[Dict[str, str]]:
    """
    Detect the column naming pattern in CSV headers.
    Returns a dict mapping standard names to actual column names, or None if not a CAN data file.
    """
    # Look for ID column pattern
    id_col = None
    data_col = None
    bus_col = None

    for header in headers:
        header_lower = header.lower()
        # Match patterns like CAN_DataFrame.CAN_DataFrame.ID or CAN_ErrorFrame.CAN_ErrorFrame.ID
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


def process_csv_files(csv_folder: str, dbc: DBCParser, output_file: Optional[str] = None,
                      changes_file: Optional[str] = None, use_extended_format: bool = False):
    """Process all CSV files in the folder and decode CAN signals.

    Args:
        use_extended_format: If True, store/display IDs in 32-bit format with extended bit (0x80000000).
                            If False, use raw 29-bit CAN ID format.
    """
    csv_files = glob.glob(os.path.join(csv_folder, '*.csv'))

    if not csv_files:
        print(f"No CSV files found in {csv_folder}")
        return

    print(f"Found {len(csv_files)} CSV files to process")

    # Collect all decoded data
    all_decoded: List[Dict] = []
    messages_found = set()
    messages_decoded = set()
    message_frame_counts: Dict[int, int] = defaultdict(int)  # Count frames per message ID

    # Initialize change tracker
    change_tracker = SignalChangeTracker()

    # Special tracker for message ID 2566871591 (Clamp15)
    CLAMP15_MSG_ID = 2566871591
    clamp15_tracker = SignalChangeTracker()
    clamp15_frame_count = 0

    # Collect all rows first to sort by timestamp
    all_rows = []
    skipped_files = 0

    for csv_file in sorted(csv_files):
        print(f"Processing: {os.path.basename(csv_file)}")

        with open(csv_file, 'r', newline='') as f:
            reader = csv.DictReader(f)

            # Detect column names from headers
            if reader.fieldnames is None:
                print(f"  Skipping: No headers found")
                skipped_files += 1
                continue

            col_map = detect_column_names(reader.fieldnames)
            if col_map is None:
                print(f"  Skipping: Not a CAN DataFrame file (missing ID/DataBytes columns)")
                skipped_files += 1
                continue

            for row in reader:
                try:
                    timestamp = float(row['timestamps'])
                    # Store row with its column mapping
                    all_rows.append((timestamp, row, col_map))
                except Exception as e:
                    print(f"  Error reading row: {e}")
                    continue

    if skipped_files > 0:
        print(f"Skipped {skipped_files} non-CAN-data files (error frames, etc.)")

    # Sort all rows by timestamp to ensure correct change detection
    print(f"Sorting {len(all_rows)} rows by timestamp...")
    all_rows.sort(key=lambda x: x[0])

    # Process sorted rows
    for timestamp, row, col_map in all_rows:
        try:
            msg_id = int(float(row[col_map['id']]))
            data_bytes_str = row[col_map['databytes']]
            bus_channel = int(float(row[col_map['buschannel']]))

            messages_found.add(msg_id)
            message_frame_counts[msg_id] += 1

            # Get message definition from DBC
            # Try direct lookup first
            message = dbc.get_message(msg_id)

            # If not found and it's an extended ID (29-bit), try with extended bit set
            # DBC files often store extended IDs with bit 31 set (0x80000000)
            if message is None and msg_id > 0x7FF:
                extended_id = msg_id | 0x80000000
                message = dbc.get_message(extended_id)

            # Also try the reverse - if the CSV has the extended bit set, try without it
            if message is None and (msg_id & 0x80000000):
                raw_id = msg_id & 0x1FFFFFFF
                message = dbc.get_message(raw_id)

            if message is None:
                continue

            messages_decoded.add(msg_id)

            # Parse data bytes
            data = parse_data_bytes(data_bytes_str)

            # Decode each signal
            decoded_signals = {}
            for signal in message.signals:
                try:
                    value = CANDecoder.extract_signal(data, signal)
                    decoded_signals[signal.name] = {
                        'value': value,
                        'unit': signal.unit
                    }

                    # Track signal changes
                    change_tracker.update(signal.name, value, timestamp)

                    # Special tracking for Clamp15 message
                    if msg_id == CLAMP15_MSG_ID:
                        clamp15_tracker.update(signal.name, value, timestamp)

                except Exception as e:
                    decoded_signals[signal.name] = {'error': str(e)}

            # Count Clamp15 frames
            if msg_id == CLAMP15_MSG_ID:
                clamp15_frame_count += 1

            # Format message ID based on user preference
            is_extended_id = msg_id > 0x7FF
            formatted_id, formatted_hex = format_message_id(msg_id, is_extended_id, use_extended_format)

            all_decoded.append({
                'timestamp': timestamp,
                'bus_channel': bus_channel,
                'message_id': formatted_id,
                'message_id_hex': formatted_hex,
                'message_name': message.name,
                'raw_data': data.hex(),
                'signals': decoded_signals
            })

        except Exception as e:
            print(f"  Error processing row: {e}")
            continue

    # Print summary
    print(f"\n{'='*60}")
    print("DECODING SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique message IDs in CSV files: {len(messages_found)}")
    print(f"Messages matched in DBC: {len(messages_decoded)}")
    print(f"Messages not in DBC: {len(messages_found - messages_decoded)}")
    print(f"Total decoded frames: {len(all_decoded)}")

    # Analyze unmatched messages - check for extended (J1939) IDs
    unmatched_ids = messages_found - messages_decoded
    standard_unmatched = [i for i in unmatched_ids if i <= 0x7FF]  # 11-bit standard CAN
    extended_unmatched = [i for i in unmatched_ids if i > 0x7FF]   # 29-bit extended CAN

    if unmatched_ids:
        print(f"\nUnmatched message ID analysis:")
        print(f"  Standard CAN IDs (11-bit, <= 0x7FF): {len(standard_unmatched)}")
        print(f"  Extended CAN IDs (29-bit, J1939): {len(extended_unmatched)}")

        if extended_unmatched and len(extended_unmatched) > len(standard_unmatched):
            print(f"\n  NOTE: Most unmatched IDs are extended (J1939) format.")
            print(f"  Your DBC file may not contain J1939 message definitions.")
            print(f"  Consider using a J1939 DBC file for these messages.")

    # Export all unique message IDs to JSON
    unique_ids_file = os.path.join(csv_folder, '..', 'unique_message_ids.json')
    unique_ids_data = []
    for msg_id in sorted(messages_found):
        in_dbc = msg_id in messages_decoded
        msg_name = dbc.get_message(msg_id).name if in_dbc else "NOT_IN_DBC"
        is_extended = msg_id > 0x7FF

        # Format ID based on user preference
        formatted_id, formatted_hex = format_message_id(msg_id, is_extended, use_extended_format)

        entry = {
            "message_id": formatted_id,
            "message_id_hex": formatted_hex,
            "message_name": msg_name,
            "frame_count": message_frame_counts[msg_id],
            "in_dbc": in_dbc,
            "is_extended_id": is_extended
        }

        # For J1939 extended IDs, add PGN info (always use raw 29-bit ID for PGN calculation)
        if is_extended:
            raw_id = msg_id & 0x1FFFFFFF  # Ensure we have 29-bit ID for PGN calc
            priority = (raw_id >> 26) & 0x7
            data_page = (raw_id >> 24) & 0x1
            pdu_format = (raw_id >> 16) & 0xFF
            pdu_specific = (raw_id >> 8) & 0xFF
            source_addr = raw_id & 0xFF

            if pdu_format < 240:
                pgn = (data_page << 16) | (pdu_format << 8)
            else:
                pgn = (data_page << 16) | (pdu_format << 8) | pdu_specific

            entry["j1939"] = {
                "pgn": pgn,
                "pgn_hex": f"0x{pgn:04X}",
                "source_address": source_addr,
                "priority": priority
            }

        unique_ids_data.append(entry)

    with open(unique_ids_file, 'w') as f:
        json.dump(unique_ids_data, f, indent=2)
    print(f"\nUnique message IDs exported to {unique_ids_file}")

    # Show decoded message types
    if messages_decoded:
        print(f"\nDecoded message types:")
        for msg_id in sorted(messages_decoded):
            msg = dbc.get_message(msg_id)
            if msg:
                frame_count = message_frame_counts[msg_id]
                is_ext = msg_id > 0x7FF
                disp_id, disp_hex = format_message_id(msg_id, is_ext, use_extended_format)
                print(f"  {disp_id} ({disp_hex}) - {msg.name} - {len(msg.signals)} signals - {frame_count} frames")

    # Print signal change statistics
    change_tracker.print_summary()

    # Export Clamp15 specific data
    clamp15_output = {
        "message_id": CLAMP15_MSG_ID,
        "message_id_hex": f"0x{CLAMP15_MSG_ID:08X}",
        "message_name": dbc.get_message(CLAMP15_MSG_ID).name if dbc.get_message(CLAMP15_MSG_ID) else "NOT_FOUND",
        "total_frames": clamp15_frame_count,
        "signals": []
    }
    clamp15_stats = clamp15_tracker.get_statistics()
    for stat in clamp15_stats:
        clamp15_output["signals"].append({
            "signal_name": stat["signal_name"],
            "change_count": stat["change_count"],
            "changes": stat["changes"]
        })

    clamp15_file = os.path.join(csv_folder, '..', 'clamp15changes.json')
    with open(clamp15_file, 'w') as f:
        json.dump(clamp15_output, f, indent=2)
    print(f"\nClamp15 (ID {CLAMP15_MSG_ID}) data exported to {clamp15_file}")
    print(f"  Total frames: {clamp15_frame_count}")
    for stat in clamp15_stats:
        print(f"  Signal '{stat['signal_name']}': {stat['change_count']} changes")

    # Export change data if requested
    if changes_file:
        change_tracker.export_to_json(changes_file)

    # Output results
    if output_file:
        write_output(all_decoded, output_file)
    else:
        # Print sample decoded data
        print(f"\n{'='*60}")
        print("SAMPLE DECODED DATA (first 20 frames)")
        print(f"{'='*60}")
        for entry in all_decoded[:20]:
            print(f"\n[{entry['timestamp']:.6f}s] {entry['message_name']} ({entry['message_id_hex']})")
            print(f"  Raw: {entry['raw_data']}")
            for sig_name, sig_data in entry['signals'].items():
                if 'value' in sig_data:
                    unit = sig_data['unit'] if sig_data['unit'] else ''
                    print(f"  {sig_name}: {sig_data['value']:.4f} {unit}")
                else:
                    print(f"  {sig_name}: ERROR - {sig_data.get('error', 'unknown')}")

    return all_decoded, change_tracker


def write_output(decoded_data: List[Dict], output_file: str):
    """Write decoded data to output file (CSV or JSON)."""
    if output_file.endswith('.json'):
        with open(output_file, 'w') as f:
            json.dump(decoded_data, f, indent=2)
        print(f"\nDecoded data written to {output_file}")
    else:
        # CSV output - flatten signals
        if not decoded_data:
            print("No data to write")
            return

        # Get all unique signal names
        all_signals = set()
        for entry in decoded_data:
            all_signals.update(entry['signals'].keys())

        fieldnames = ['timestamp', 'bus_channel', 'message_id_hex', 'message_name', 'raw_data']
        fieldnames.extend(sorted(all_signals))

        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for entry in decoded_data:
                row = {
                    'timestamp': entry['timestamp'],
                    'bus_channel': entry['bus_channel'],
                    'message_id_hex': entry['message_id_hex'],
                    'message_name': entry['message_name'],
                    'raw_data': entry['raw_data']
                }
                for sig_name, sig_data in entry['signals'].items():
                    if 'value' in sig_data:
                        row[sig_name] = sig_data['value']
                    else:
                        row[sig_name] = 'ERROR'
                writer.writerow(row)

        print(f"\nDecoded data written to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Decode CAN signals from CSV files using a DBC file'
    )
    parser.add_argument('dbc_file', help='Path to the DBC file')
    parser.add_argument('csv_folder', help='Path to folder containing CSV files')
    parser.add_argument('--output', '-o', help='Output file for decoded data (CSV or JSON)')
    parser.add_argument('--changes', '-c', help='Output file for signal changes (JSON)')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress verbose DBC message listing')
    parser.add_argument('--id-format', choices=['29bit', '32bit'], default='29bit',
                        help='ID format for output: 29bit (raw CAN ID) or 32bit (with extended bit 0x80000000). Default: 29bit')

    args = parser.parse_args()

    # Validate inputs
    if not os.path.isfile(args.dbc_file):
        print(f"Error: DBC file not found: {args.dbc_file}")
        sys.exit(1)

    if not os.path.isdir(args.csv_folder):
        print(f"Error: CSV folder not found: {args.csv_folder}")
        sys.exit(1)

    # Parse DBC file
    print(f"Loading DBC file: {args.dbc_file}")
    dbc = DBCParser(args.dbc_file, verbose=not args.quiet)

    # Set ID format preference
    use_extended_format = (args.id_format == '32bit')
    print(f"ID format: {'32-bit (with extended bit)' if use_extended_format else '29-bit (raw CAN ID)'}")

    # Process CSV files
    process_csv_files(args.csv_folder, dbc, args.output, args.changes, use_extended_format)


if __name__ == '__main__':
    main()
