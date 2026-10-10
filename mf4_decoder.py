#!/usr/bin/env python3
"""
MF4 CAN Signal Decoder - Reads MF4 files directly and decodes signals using a DBC file.

Usage:
    python mf4_decoder.py <dbc_file> <mf4_file> [--output <output_file>] [--changes <changes_file>]

Requirements:
    pip install asammdf
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from asammdf import MDF
    ASAMMDF_AVAILABLE = True
except ImportError:
    ASAMMDF_AVAILABLE = False


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

        msg_pattern = re.compile(
            r'BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)'
        )

        sig_pattern = re.compile(
            r'^\s*SG_\s+(\w+)\s*(?:(M|m\d+)\s+)?:\s*(\d+)\|(\d+)@([01])([+-])\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)\s*\[\s*([^|]*)\|([^\]]*)\s*\]\s*"([^"]*)"\s*(.*)$'
        )

        current_message = None
        total_signals_parsed = 0
        multiplexed_signals = 0

        for line in content.split('\n'):
            line_stripped = line.strip()

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

            sig_match = sig_pattern.match(line)
            if sig_match and current_message:
                sig_name = sig_match.group(1)
                mux_indicator = sig_match.group(2)
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

        if signal.is_signed and signal.length > 0:
            max_val = 1 << (signal.length - 1)
            if raw_value >= max_val:
                raw_value -= (1 << signal.length)

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
    """Tracks value changes for all signals with streaming output support.

    Memory-optimized: only stores last value and change count per signal.
    Changes are streamed directly to file if output_file is provided.
    """

    def __init__(self, output_file: Optional[str] = None):
        """Initialize tracker with optional streaming output.

        Args:
            output_file: If provided, changes are streamed to this file.
                        If None, changes are stored in memory (not recommended for large files).
        """
        # Lightweight tracking: only last value and count per signal
        self.signal_stats: Dict[str, Dict] = defaultdict(lambda: {
            "last_value": None,
            "change_count": 0,
            "first_value": None,
            "first_timestamp": None,
            "last_timestamp": None
        })

        # Streaming output
        self.output_file = output_file
        self.file_handle = None
        self.temp_files: Dict[str, str] = {}  # signal_name -> temp file path
        self.temp_handles: Dict[str, any] = {}  # signal_name -> file handle

        if output_file:
            # Create temp directory for per-signal change files
            import tempfile
            self.temp_dir = tempfile.mkdtemp(prefix='signal_changes_')

    def update(self, signal_name: str, value: float, timestamp: float):
        """Update signal value and track if it changed."""
        stats = self.signal_stats[signal_name]

        is_change = False
        if stats["last_value"] is None:
            # First value for this signal
            stats["first_value"] = value
            stats["first_timestamp"] = timestamp
            stats["last_value"] = value
            stats["last_timestamp"] = timestamp
            is_change = True
        elif stats["last_value"] != value:
            # Value changed
            stats["change_count"] += 1
            stats["last_value"] = value
            stats["last_timestamp"] = timestamp
            is_change = True

        # Stream to per-signal temp file if output is enabled
        if is_change and self.output_file:
            self._write_change_to_temp(signal_name, value, timestamp)

    def _write_change_to_temp(self, signal_name: str, value: float, timestamp: float):
        """Write a change to the per-signal temp file."""
        if signal_name not in self.temp_handles:
            import os
            # Create temp file for this signal
            safe_name = signal_name.replace('/', '_').replace('\\', '_')
            temp_path = os.path.join(self.temp_dir, f"{safe_name}.jsonl")
            self.temp_files[signal_name] = temp_path
            self.temp_handles[signal_name] = open(temp_path, 'w')

        # Write as JSON line
        self.temp_handles[signal_name].write(json.dumps({"value": value, "timestamp": timestamp}) + '\n')

    def get_statistics_summary(self) -> List[Dict]:
        """Get lightweight statistics (no full change list)."""
        result = []
        for signal_name, stats in sorted(self.signal_stats.items()):
            result.append({
                "signal_name": signal_name,
                "change_count": stats["change_count"],
                "first_value": stats["first_value"],
                "last_value": stats["last_value"],
                "first_timestamp": stats["first_timestamp"],
                "last_timestamp": stats["last_timestamp"]
            })
        return result

    def print_summary(self, top_n: int = 20):
        """Print summary of signal changes."""
        stats = self.get_statistics_summary()
        stats_sorted = sorted(stats, key=lambda x: x["change_count"], reverse=True)

        print(f"\n{'='*60}")
        print("SIGNAL CHANGE STATISTICS")
        print(f"{'='*60}")
        print(f"Total unique signals tracked: {len(stats)}")
        print(f"\nTop {top_n} signals by change count:")
        print(f"{'Signal Name':<45} {'Changes':>12}")
        print("-" * 58)

        for stat in stats_sorted[:top_n]:
            print(f"{stat['signal_name']:<45} {stat['change_count']:>12}")

        unchanged = [s for s in stats if s["change_count"] == 0]
        if unchanged:
            print(f"\nSignals that never changed: {len(unchanged)}")

    def finalize(self):
        """Finalize and merge all temp files into the final output."""
        if not self.output_file:
            return

        # Close all temp file handles
        for handle in self.temp_handles.values():
            handle.close()

        # Merge temp files into final output
        print(f"\nMerging signal changes to {self.output_file}...")

        with open(self.output_file, 'w') as out:
            out.write('[\n')
            first_signal = True

            for signal_name in sorted(self.signal_stats.keys()):
                stats = self.signal_stats[signal_name]

                if not first_signal:
                    out.write(',\n')
                first_signal = False

                # Write signal header
                out.write('  {\n')
                out.write(f'    "signal_name": {json.dumps(signal_name)},\n')
                out.write(f'    "change_count": {stats["change_count"]},\n')
                out.write('    "changes": [\n')

                # Read changes from temp file
                if signal_name in self.temp_files:
                    import os
                    temp_path = self.temp_files[signal_name]
                    if os.path.exists(temp_path):
                        with open(temp_path, 'r') as temp_in:
                            first_change = True
                            for line in temp_in:
                                if not first_change:
                                    out.write(',\n')
                                first_change = False
                                change = json.loads(line.strip())
                                out.write(f'      {{"value": {change["value"]}, "timestamp": {change["timestamp"]}}}')

                out.write('\n    ]\n')
                out.write('  }')

            out.write('\n]\n')

        # Cleanup temp files
        self._cleanup_temp_files()

        print(f"Signal changes exported to {self.output_file}")

    def _cleanup_temp_files(self):
        """Remove temp files and directory."""
        import os
        import shutil

        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def export_to_json(self, output_file: str):
        """Export change data to JSON file (legacy method for compatibility)."""
        if self.output_file:
            # Already streaming, just finalize
            self.finalize()
        else:
            # No streaming was set up, export summary only
            stats = self.get_statistics_summary()

            output = []
            for stat in stats:
                output.append({
                    "signal_name": stat["signal_name"],
                    "change_count": stat["change_count"],
                    "changes": []  # Empty - not tracked in memory mode
                })

            with open(output_file, 'w') as f:
                json.dump(output, f, indent=2)

            print(f"\nSignal change summary exported to {output_file}")
            print(f"Note: Full change history not available (memory optimization mode)")


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


def process_mf4_file(mf4_path: str, dbc: DBCParser, output_file: Optional[str] = None,
                     changes_file: Optional[str] = None, use_extended_format: bool = False):
    """Process MF4 file and decode CAN signals.

    Uses streaming output to handle large files without running out of memory.

    Args:
        use_extended_format: If True, store/display IDs in 32-bit format with extended bit (0x80000000).
                            If False, use raw 29-bit CAN ID format.
    """
    import csv

    print(f"\nOpening MF4 file: {mf4_path}")
    file_size_mb = os.path.getsize(mf4_path) / (1024 * 1024)
    print(f"File size: {file_size_mb:.1f} MB")

    # Open MF4 file with memory optimization for large files
    mdf = MDF(mf4_path, memory='minimum')

    print(f"MF4 file contains {len(mdf.groups)} channel groups")

    # Statistics tracking (lightweight - just counts and sets)
    messages_found = set()
    messages_decoded = set()
    message_frame_counts: Dict[int, int] = defaultdict(int)

    # Initialize change tracker with streaming output if changes_file is specified
    change_tracker = SignalChangeTracker(output_file=changes_file)

    # Sample buffer for console output (only keep first 10 decoded frames)
    sample_frames: List[Dict] = []
    MAX_SAMPLES = 10

    # Track all signal names seen (for CSV header)
    all_signal_names: set = set()

    total_frames_processed = 0
    total_decoded_frames = 0

    # Streaming output setup
    output_handle = None
    csv_writer = None
    json_first_entry = True

    if output_file:
        if output_file.endswith('.json'):
            output_handle = open(output_file, 'w')
            output_handle.write('[\n')  # Start JSON array
        else:
            # For CSV, we need to collect signal names first, then write
            # We'll use a temp approach: write to temp file, then rename
            pass  # CSV handled after processing

    # Iterate through channel groups
    for group_idx, group in enumerate(mdf.groups):
        # Get channel names in this group
        channel_names = [ch.name for ch in group.channels]

        # Look for CAN data channels (ID and DataBytes)
        id_channel = None
        data_channel = None
        bus_channel = None

        for ch_name in channel_names:
            ch_lower = ch_name.lower()
            if ch_lower.endswith('.id') and 'error' not in ch_lower:
                id_channel = ch_name
            elif ch_lower.endswith('.databytes') and 'error' not in ch_lower:
                data_channel = ch_name
            elif ch_lower.endswith('.buschannel') and 'error' not in ch_lower:
                bus_channel = ch_name

        if not id_channel or not data_channel:
            continue

        print(f"Processing group {group_idx}: {id_channel.split('.')[0]}")

        try:
            # Get the signals from this group
            id_signal = mdf.get(id_channel, group=group_idx)
            data_signal = mdf.get(data_channel, group=group_idx)

            timestamps = id_signal.timestamps
            ids = id_signal.samples
            data_bytes_array = data_signal.samples

            bus_values = None
            if bus_channel:
                try:
                    bus_signal = mdf.get(bus_channel, group=group_idx)
                    bus_values = bus_signal.samples
                except:
                    pass

            num_frames = len(timestamps)
            print(f"  Found {num_frames} frames")

            # Process frames
            for i in range(num_frames):
                try:
                    timestamp = float(timestamps[i])
                    msg_id = int(ids[i])

                    # Handle data bytes - can be array or bytes
                    raw_data = data_bytes_array[i]
                    if hasattr(raw_data, 'tobytes'):
                        data = raw_data.tobytes()
                    elif isinstance(raw_data, (bytes, bytearray)):
                        data = bytes(raw_data)
                    elif hasattr(raw_data, '__iter__'):
                        data = bytes([int(b) for b in raw_data])
                    else:
                        continue

                    bus = int(bus_values[i]) if bus_values is not None else 0

                    messages_found.add(msg_id)
                    message_frame_counts[msg_id] += 1

                    # Get message definition from DBC
                    message = dbc.get_message(msg_id)

                    # Try with extended bit set
                    if message is None and msg_id > 0x7FF:
                        extended_id = msg_id | 0x80000000
                        message = dbc.get_message(extended_id)

                    # Try without extended bit
                    if message is None and (msg_id & 0x80000000):
                        raw_id = msg_id & 0x1FFFFFFF
                        message = dbc.get_message(raw_id)

                    if message is None:
                        continue

                    messages_decoded.add(msg_id)

                    # Decode each signal
                    decoded_signals = {}
                    for signal in message.signals:
                        try:
                            value = CANDecoder.extract_signal(data, signal)
                            decoded_signals[signal.name] = {
                                'value': value,
                                'unit': signal.unit
                            }
                            all_signal_names.add(signal.name)

                            # Track signal changes
                            change_tracker.update(signal.name, value, timestamp)

                        except Exception as e:
                            decoded_signals[signal.name] = {'error': str(e)}

                    # Format message ID based on user preference
                    is_extended = msg_id > 0x7FF
                    formatted_id, formatted_id_hex = format_message_id(msg_id, is_extended, use_extended_format)

                    # Create decoded entry
                    entry = {
                        'timestamp': timestamp,
                        'bus_channel': bus,
                        'message_id': formatted_id,
                        'message_id_hex': formatted_id_hex,
                        'message_name': message.name,
                        'raw_data': data.hex(),
                        'signals': decoded_signals
                    }

                    total_decoded_frames += 1

                    # Keep sample for console output
                    if len(sample_frames) < MAX_SAMPLES:
                        sample_frames.append(entry)

                    # Stream to JSON output file
                    if output_handle and output_file.endswith('.json'):
                        if not json_first_entry:
                            output_handle.write(',\n')
                        json.dump(entry, output_handle)
                        json_first_entry = False

                    total_frames_processed += 1

                    # Progress indicator for large files
                    if total_frames_processed % 100000 == 0:
                        print(f"  Processed {total_frames_processed} decoded frames...")

                except Exception as e:
                    continue

        except Exception as e:
            print(f"  Error processing group {group_idx}: {e}")
            continue

    mdf.close()

    # Close JSON output file
    if output_handle and output_file.endswith('.json'):
        output_handle.write('\n]')
        output_handle.close()
        print(f"\nDecoded data streamed to {output_file}")

    # Print summary
    print(f"\n{'='*60}")
    print("DECODING SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique message IDs in MF4 file: {len(messages_found)}")
    print(f"Messages matched in DBC: {len(messages_decoded)}")
    print(f"Messages not in DBC: {len(messages_found - messages_decoded)}")
    print(f"Total decoded frames: {total_decoded_frames}")

    # Analyze unmatched messages
    unmatched_ids = messages_found - messages_decoded
    standard_unmatched = [i for i in unmatched_ids if i <= 0x7FF]
    extended_unmatched = [i for i in unmatched_ids if i > 0x7FF]

    if unmatched_ids:
        print(f"\nUnmatched message ID analysis:")
        print(f"  Standard CAN IDs (11-bit, <= 0x7FF): {len(standard_unmatched)}")
        print(f"  Extended CAN IDs (29-bit, J1939): {len(extended_unmatched)}")

        if extended_unmatched and len(extended_unmatched) > len(standard_unmatched):
            print(f"\n  NOTE: Most unmatched IDs are extended (J1939) format.")
            print(f"  Your DBC file may not contain J1939 message definitions.")

    # Export unique message IDs
    output_dir = os.path.dirname(mf4_path) or '.'
    unique_ids_file = os.path.join(output_dir, 'unique_message_ids.json')
    unique_ids_data = []

    for msg_id in sorted(messages_found):
        in_dbc = msg_id in messages_decoded
        msg_name = dbc.get_message(msg_id).name if in_dbc else "NOT_IN_DBC"
        is_extended = msg_id > 0x7FF

        # Format message ID based on user preference
        formatted_id, formatted_id_hex = format_message_id(msg_id, is_extended, use_extended_format)

        entry = {
            "message_id": formatted_id,
            "message_id_hex": formatted_id_hex,
            "message_name": msg_name,
            "frame_count": message_frame_counts[msg_id],
            "in_dbc": in_dbc,
            "is_extended_id": is_extended
        }

        if is_extended:
            priority = (msg_id >> 26) & 0x7
            data_page = (msg_id >> 24) & 0x1
            pdu_format = (msg_id >> 16) & 0xFF
            pdu_specific = (msg_id >> 8) & 0xFF
            source_addr = msg_id & 0xFF

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
                is_extended = msg_id > 0x7FF
                formatted_id, formatted_id_hex = format_message_id(msg_id, is_extended, use_extended_format)
                print(f"  {formatted_id} ({formatted_id_hex}) - {msg.name} - {len(msg.signals)} signals - {frame_count} frames")

    # Print signal change statistics
    change_tracker.print_summary()

    # Finalize and export change data (streaming mode already wrote to temp files)
    if changes_file:
        change_tracker.finalize()

    # Handle CSV output (requires second pass for proper headers, or write without signal columns)
    if output_file and not output_file.endswith('.json'):
        print(f"\nNote: For large files, JSON output is recommended for streaming.")
        print(f"CSV output requires all signal names to be known upfront.")
        # Write CSV with the signals we've collected
        if total_decoded_frames > 0:
            fieldnames = ['timestamp', 'bus_channel', 'message_id_hex', 'message_name', 'raw_data']
            fieldnames.extend(sorted(all_signal_names))

            # For CSV, we need to re-process or use sample data
            # Since we want to avoid memory issues, just write sample for CSV
            print(f"Writing sample data to CSV (first {len(sample_frames)} frames)...")
            with open(output_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for entry in sample_frames:
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
            print(f"Sample CSV written to {output_file}")
            print(f"For full data export, use JSON format: --output output.json")

    # Print sample decoded data if no output file
    if not output_file:
        print(f"\n{'='*60}")
        print(f"SAMPLE DECODED DATA (first {len(sample_frames)} frames)")
        print(f"{'='*60}")
        for entry in sample_frames:
            print(f"\n[{entry['timestamp']:.6f}s] {entry['message_name']} ({entry['message_id_hex']})")
            print(f"  Raw: {entry['raw_data']}")
            for sig_name, sig_data in entry['signals'].items():
                if 'value' in sig_data:
                    unit = sig_data['unit'] if sig_data['unit'] else ''
                    print(f"  {sig_name}: {sig_data['value']:.4f} {unit}")
                else:
                    print(f"  {sig_name}: ERROR - {sig_data.get('error', 'unknown')}")

    return sample_frames, change_tracker


def write_output(decoded_data: List[Dict], output_file: str):
    """Write decoded data to output file (CSV or JSON)."""
    import csv

    if output_file.endswith('.json'):
        with open(output_file, 'w') as f:
            json.dump(decoded_data, f, indent=2)
        print(f"\nDecoded data written to {output_file}")
    else:
        if not decoded_data:
            print("No data to write")
            return

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
        description='Decode CAN signals from MF4 files using a DBC file'
    )
    parser.add_argument('dbc_file', help='Path to the DBC file')
    parser.add_argument('mf4_file', help='Path to the MF4 file')
    parser.add_argument('--output', '-o', help='Output file for decoded data (CSV or JSON)')
    parser.add_argument('--changes', '-c', help='Output file for signal changes (JSON)')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress verbose DBC message listing')
    parser.add_argument('--id-format', choices=['29bit', '32bit'], default='29bit',
                        help='ID format for output: 29bit (raw CAN ID) or 32bit (with extended bit 0x80000000). Default: 29bit')

    args = parser.parse_args()

    # Check for asammdf
    if not ASAMMDF_AVAILABLE:
        print("Error: asammdf library is required for MF4 file support.")
        print("Install it with: pip install asammdf")
        sys.exit(1)

    # Validate inputs
    if not os.path.isfile(args.dbc_file):
        print(f"Error: DBC file not found: {args.dbc_file}")
        sys.exit(1)

    if not os.path.isfile(args.mf4_file):
        print(f"Error: MF4 file not found: {args.mf4_file}")
        sys.exit(1)

    # Parse DBC file
    print(f"Loading DBC file: {args.dbc_file}")
    dbc = DBCParser(args.dbc_file, verbose=not args.quiet)

    # Set ID format preference
    use_extended_format = (args.id_format == '32bit')
    print(f"ID format: {'32-bit (with extended bit)' if use_extended_format else '29-bit (raw CAN ID)'}")

    # Process MF4 file
    process_mf4_file(args.mf4_file, dbc, args.output, args.changes, use_extended_format)


if __name__ == '__main__':
    main()
