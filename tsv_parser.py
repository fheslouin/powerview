#!/usr/bin/env python3
"""
TSV to InfluxDB 3 Core Parser
Recursively parses TSV files and loads data into InfluxDB database
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple
import pandas as pd
from influxdb_client_3 import (
    InfluxDBClient3, InfluxDBError, Point, WritePrecision,
    WriteOptions, write_client_options
)
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def parse_tsv_header(tsv_file: str) -> Tuple[List[Dict], str]:
    """
    Parse the first two lines of TSV file to extract device and channel information.

    Returns:
        Tuple of (channel_mappings, campaign_name)
        channel_mappings: List of dicts with device, channel info
        campaign_name: The first column value of line 2
    """
    with open(tsv_file, 'r', encoding='utf-8') as f:
        line1 = f.readline().strip().split('\t')  # Device serial numbers
        line2 = f.readline().strip().split('\t')  # Channel names with units

    # First column of line 2 is campaign identifier
    campaign_name = line2[0]

    # Build channel mappings
    channel_mappings = []
    device_channel_counter = {}

    for col_idx in range(1, len(line1)):
        device_serial = line1[col_idx]
        channel_info = line2[col_idx]

        # Track channel index per device
        if device_serial not in device_channel_counter:
            device_channel_counter[device_serial] = 0
        device_channel_counter[device_serial] += 1
        channel_idx = device_channel_counter[device_serial]

        # Parse channel name and unit
        parts = channel_info.rsplit(' ', 1)
        if len(parts) == 2:
            channel_name, unit = parts
        else:
            channel_name = channel_info
            unit = ''

        channel_mappings.append({
            'column_idx': col_idx,
            'device': device_serial,
            'channel_idx': channel_idx,
            'channel_name': channel_name.strip(),
            'unit': unit.strip()
        })

    return channel_mappings, campaign_name


def parse_tsv_data(tsv_file: str, channel_mappings: List[Dict], campaign: str,
                   database_name: str, table_name: str) -> List[Point]:
    """
    Parse TSV data rows and create InfluxDB Points.

    Args:
        tsv_file: Path to TSV file
        channel_mappings: Channel mapping information from header
        campaign: Campaign name from folder structure
        database_name: Database name (top folder)
        table_name: Table name (campaign folder)

    Returns:
        List of InfluxDB Point objects
    """
    # Read data starting from line 3 (skip header lines)
    df = pd.read_csv(tsv_file, sep='\t', skiprows=2, header=None)

    points = []

    for _, row in df.iterrows():
        # First column is timestamp
        timestamp_str = str(row[0])

        # Parse timestamp (format: MM/DD/YY HH:MM:SS)
        try:
            timestamp = datetime.strptime(timestamp_str, '%m/%d/%y %H:%M:%S')
        except ValueError:
            try:
                timestamp = datetime.strptime(timestamp_str, '%d/%m/%y %H:%M:%S')
            except ValueError:
                print(f"Warning: Could not parse timestamp: {timestamp_str}")
                continue

        # Create a point for each channel
        for mapping in channel_mappings:
            col_idx = mapping['column_idx']

            # Get value from dataframe
            try:
                value = float(row[col_idx])
            except (ValueError, KeyError):
                print(f"Warning: Invalid value at column {col_idx}")
                continue

            # Create point with table name
            point = Point(table_name)

            # Add tags
            point = point.tag('device', mapping['device'])
            point = point.tag('channel_idx', str(mapping['channel_idx']))
            point = point.tag('channel_name', mapping['channel_name'])
            point = point.tag('unit', mapping['unit'])
            point = point.tag('campaign', campaign)

            # Add field (the actual measurement value)
            point = point.field('value', value)

            # Set timestamp (Unix timestamp in seconds)
            point = point.time(int(timestamp.timestamp()), WritePrecision.S)

            points.append(point)

    return points


def extract_path_components(tsv_path: str, base_folder: str) -> Tuple[str, str, str]:
    """
    Extract database name, campaign name, and device serial from file path.

    Structure: base_folder/my_client/campaign/device_serial/file.tsv

    Returns:
        Tuple of (database_name, campaign_name, device_serial)
    """
    path = Path(tsv_path)
    relative_path = path.relative_to(base_folder)
    parts = relative_path.parts

    if len(parts) < 4:
        raise ValueError(f"Invalid path structure: {tsv_path}")

    database_name = parts[0]  # my_client (top folder)
    campaign_name = parts[1]  # campaign folder
    device_serial = parts[2]  # device serial number folder

    return database_name, campaign_name, device_serial


def process_tsv_file(tsv_file: str, base_folder: str, client: InfluxDBClient3) -> bool:
    """
    Process a single TSV file and write to InfluxDB.

    Returns:
        True if successful, False otherwise
    """
    try:
        print(f"Processing: {tsv_file}")

        # Extract path components
        database_name, campaign_name, device_serial = extract_path_components(
            tsv_file, base_folder
        )

        # Parse TSV header
        channel_mappings, _ = parse_tsv_header(tsv_file)

        print(f"  Database: {database_name}")
        print(f"  Campaign: {campaign_name}")
        print(f"  Device: {device_serial}")
        print(f"  Channels: {len(channel_mappings)}")

        # Parse data and create points
        points = parse_tsv_data(
            tsv_file,
            channel_mappings,
            campaign_name,
            database_name,
            campaign_name  # table name is campaign name
        )

        print(f"  Points created: {len(points)}")

        # Write to InfluxDB
        if points:
            # Update client to use correct database
            client._database = database_name
            client.write(points, write_precision='s')
            print(f"  ✓ Successfully written to InfluxDB")

        return True

    except Exception as e:
        print(f"  ✗ Error processing {tsv_file}: {str(e)}")
        return False


def rename_parsed_file(tsv_file: str) -> None:
    """
    Rename processed file by adding PARSED_ prefix.
    """
    path = Path(tsv_file)
    new_name = f"PARSED_{path.name}"
    new_path = path.parent / new_name
    path.rename(new_path)
    print(f"  Renamed to: {new_name}")


def find_tsv_files(base_folder: str) -> List[str]:
    """
    Recursively find all .tsv files that haven't been parsed yet.
    """
    tsv_files = []
    for root, dirs, files in os.walk(base_folder):
        for file in files:
            if file.endswith('.tsv') and not file.startswith('PARSED_'):
                tsv_files.append(os.path.join(root, file))
    return tsv_files


def setup_influxdb_client() -> InfluxDBClient3:
    """
    Setup InfluxDB client with configuration from environment variables.
    """
    host = os.getenv('INFLUX_HOST')
    token = os.getenv('INFLUXDB_ADMIN_TOKEN')
    database = os.getenv('INFLUX_DATABASE', 'default')

    if not host or not token:
        raise ValueError(
            "Missing required environment variables: INFLUX_HOST and INFLUXDB_ADMIN_TOKEN"
        )

    # Define callbacks for batch writing
    def success(self, data: str):
        print(f"  Successfully wrote batch")

    def error(self, data: str, exception: InfluxDBError):
        print(f"  Failed writing batch: {exception}")

    def retry(self, data: str, exception: InfluxDBError):
        print(f"  Retrying batch write: {exception}")

    # Configure write options
    write_options = WriteOptions(
        batch_size=500,
        flush_interval=10_000,
        jitter_interval=2_000,
        retry_interval=5_000,
        max_retries=5,
        max_retry_delay=30_000,
        exponential_base=2
    )

    # Create write client options
    wco = write_client_options(
        success_callback=success,
        error_callback=error,
        retry_callback=retry,
        write_options=write_options
    )

    # Create client
    client = InfluxDBClient3(
        host=host,
        token=token,
        database=database,
        write_client_options=wco
    )

    return client


def main():
    """
    Main function to process TSV files recursively.
    """
    print("=" * 70)
    print("TSV to InfluxDB 3 Core Parser")
    print("=" * 70)

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dataFolder", help="Path to the data folder (ex: /srv/powerview/data)")
    parser.add_argument("-t", "--tsvFile", help="Path to the TSV file(s)")
    args = parser.parse_args()

    # Get TSV file from command line arguments or find all TSV files
    if args.tsvFile and args.dataFolder:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            print(f"Error: Folder '{base_folder}' does not exist.")
            sys.exit(1)
        else:
            print(f"Using data folder: {base_folder}")

        tsv_files = [args.tsvFile]
        print(f"Using specified TSV files: {tsv_files}")

    elif args.dataFolder and not args.tsvFile:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            print(f"Error: Folder '{base_folder}' does not exist.")
            sys.exit(1)
        else:
            print(f"Using all TSV files in folder: {base_folder}")
            tsv_files = find_tsv_files(base_folder)

    if not tsv_files:
        print("No TSV files found to process.")
        return

    print(f"\nFound {len(tsv_files)} TSV file(s) to process.\n")

    # Setup InfluxDB client
    try:
        client = setup_influxdb_client()
        print(f"Connected to InfluxDB at {os.getenv('INFLUX_HOST')}\n")
    except Exception as e:
        print(f"Error connecting to InfluxDB: {str(e)}")
        sys.exit(1)

    # Process each file
    successful = 0
    failed = 0

    for tsv_file in tsv_files:
        if process_tsv_file(tsv_file, base_folder, client):
            rename_parsed_file(tsv_file)
            successful += 1
        else:
            failed += 1
        print()

    # Summary
    print("=" * 70)
    print(f"Processing complete!")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print("=" * 70)

    # Close client
    client.close()


if __name__ == "__main__":
    main()
