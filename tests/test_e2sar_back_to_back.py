#!/usr/bin/env python3
"""
E2SAR Back-to-Back Integration Test

This test verifies the full E2SAR integration by running a producer and consumer
in back-to-back mode (without actual EJFAT hardware) and validating the results.

Usage:
    python tests/test_e2sar_back_to_back.py
    pytest tests/test_e2sar_back_to_back.py
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

# Configuration
NUM_RANKS = 1  # Use 1 rank to avoid macOS UDP socket binding issues
CONSUMER_CONFIG = "examples/e2sar-back-to-back-consumer.yaml"
PRODUCER_CONFIG = "examples/e2sar-back-to-back-producer.yaml"
OUTPUT_DIR = "e2sar_b2b_output"
EXPECTED_NUM_FILES = 10  # 50 events / 5 batch_size = 10 batches
EXPECTED_EVENTS_PER_FILE = 5  # batch_size from producer config
EXPECTED_ARRAY_SHAPE = (100, 100)  # From producer config
EXPECTED_EVENT_ID = 42  # From producer config
CONSUMER_STARTUP_DELAY = 3  # seconds to wait for consumer to initialize
CONSUMER_SHUTDOWN_TIMEOUT = 10  # seconds to wait for graceful shutdown


def cleanup_output_directory():
    """Remove existing output directory to start fresh"""
    if os.path.exists(OUTPUT_DIR):
        print(f"Cleaning up existing output directory: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)


def start_consumer() -> subprocess.Popen:
    """Start the E2SAR consumer in the background"""
    print(f"Starting consumer with {NUM_RANKS} rank(s)...")
    cmd = ["mpirun", "-n", str(NUM_RANKS), "lclstreamer", "--config", CONSUMER_CONFIG]

    # Start consumer process
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # Wait for consumer to initialize
    print(f"Waiting {CONSUMER_STARTUP_DELAY}s for consumer to initialize...")
    time.sleep(CONSUMER_STARTUP_DELAY)

    # Check if process is still running
    if process.poll() is not None:
        output, _ = process.communicate()
        raise RuntimeError(f"Consumer failed to start:\n{output}")

    print("Consumer started successfully")
    return process


def run_producer() -> tuple[int, str]:
    """Run the E2SAR producer and return exit code and output"""
    print(f"Starting producer with {NUM_RANKS} rank(s)...")
    cmd = ["mpirun", "-n", str(NUM_RANKS), "lclstreamer", "--config", PRODUCER_CONFIG]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60
    )

    print("Producer completed")
    return result.returncode, result.stdout


def stop_consumer(process: subprocess.Popen) -> str:
    """Stop the consumer process and return its output"""
    print("Stopping consumer...")

    # Try graceful shutdown first
    process.send_signal(signal.SIGINT)

    try:
        output, _ = process.communicate(timeout=CONSUMER_SHUTDOWN_TIMEOUT)
        print("Consumer stopped gracefully")
    except subprocess.TimeoutExpired:
        print("Consumer did not stop gracefully, sending SIGKILL...")
        process.kill()
        output, _ = process.communicate()

    return output


def verify_output_files() -> list[Path]:
    """Verify that the expected output files were created"""
    print(f"\nVerifying output files in {OUTPUT_DIR}...")

    if not os.path.exists(OUTPUT_DIR):
        raise AssertionError(f"Output directory does not exist: {OUTPUT_DIR}")

    # Find all HDF5 files
    output_files = sorted(Path(OUTPUT_DIR).glob("b2b_test_*.h5"))

    print(f"Found {len(output_files)} output files:")
    for f in output_files:
        file_size = f.stat().st_size / 1024  # KB
        print(f"  {f.name} ({file_size:.1f} KB)")

    if len(output_files) != EXPECTED_NUM_FILES:
        raise AssertionError(
            f"Expected {EXPECTED_NUM_FILES} output files, found {len(output_files)}"
        )

    print(f"✓ Correct number of output files ({EXPECTED_NUM_FILES})")
    return output_files


def verify_file_contents(file_path: Path) -> dict:
    """Verify the contents of a single HDF5 file"""
    with h5py.File(file_path, 'r') as f:
        # Check for expected datasets
        if '/data/array' not in f:
            raise AssertionError(f"Missing /data/array in {file_path}")
        if '/data/event_id' not in f:
            raise AssertionError(f"Missing /data/event_id in {file_path}")

        # Read data
        array = f['/data/array'][()]
        event_id = f['/data/event_id'][()]

        # Verify array shape (should be [1, batch_size, height, width])
        expected_shape = (1, EXPECTED_EVENTS_PER_FILE, *EXPECTED_ARRAY_SHAPE)
        if array.shape != expected_shape:
            raise AssertionError(
                f"Array shape mismatch in {file_path}: "
                f"expected {expected_shape}, got {array.shape}"
            )

        # Verify array dtype
        if array.dtype != np.float32:
            raise AssertionError(
                f"Array dtype mismatch in {file_path}: "
                f"expected float32, got {array.dtype}"
            )

        # Verify array values are in valid range [0, 1] for random data
        if array.min() < 0 or array.max() > 1:
            raise AssertionError(
                f"Array values out of range in {file_path}: "
                f"[{array.min()}, {array.max()}]"
            )

        # Verify event_id shape
        expected_id_shape = (1, EXPECTED_EVENTS_PER_FILE)
        if event_id.shape != expected_id_shape:
            raise AssertionError(
                f"Event ID shape mismatch in {file_path}: "
                f"expected {expected_id_shape}, got {event_id.shape}"
            )

        # Verify all event IDs are correct
        if not np.all(event_id == EXPECTED_EVENT_ID):
            raise AssertionError(
                f"Event ID mismatch in {file_path}: "
                f"expected all {EXPECTED_EVENT_ID}, got {np.unique(event_id)}"
            )

        return {
            'array_shape': array.shape,
            'array_dtype': array.dtype,
            'array_range': (float(array.min()), float(array.max())),
            'event_id': int(event_id[0, 0])
        }


def verify_all_files(output_files: list[Path]):
    """Verify contents of all output files"""
    print(f"\nVerifying contents of {len(output_files)} files...")

    all_stats = []
    for file_path in output_files:
        try:
            stats = verify_file_contents(file_path)
            all_stats.append(stats)
        except Exception as e:
            raise AssertionError(f"Failed to verify {file_path}: {e}")

    # Print summary
    print(f"\n✓ All files contain valid data:")
    print(f"  Array shape: {all_stats[0]['array_shape']}")
    print(f"  Array dtype: {all_stats[0]['array_dtype']}")
    print(f"  Array range: [{all_stats[0]['array_range'][0]:.4f}, "
          f"{all_stats[0]['array_range'][1]:.4f}]")
    print(f"  Event ID: {all_stats[0]['event_id']}")


def run_back_to_back_test():
    """Run the complete E2SAR back-to-back test"""
    print("=" * 70)
    print("E2SAR Back-to-Back Integration Test")
    print("=" * 70)

    consumer_process: Optional[subprocess.Popen] = None

    try:
        # Step 1: Clean up
        cleanup_output_directory()

        # Step 2: Start consumer
        consumer_process = start_consumer()

        # Step 3: Run producer
        producer_exit_code, producer_output = run_producer()

        if producer_exit_code != 0:
            print("\nProducer output:")
            print(producer_output)
            raise RuntimeError(f"Producer failed with exit code {producer_exit_code}")

        # Step 4: Stop consumer
        consumer_output = stop_consumer(consumer_process)
        consumer_process = None  # Mark as cleaned up

        # Step 5: Verify outputs
        output_files = verify_output_files()
        verify_all_files(output_files)

        # Success!
        print("\n" + "=" * 70)
        print("✓ E2SAR Back-to-Back Test PASSED")
        print("=" * 70)

        return True

    except Exception as e:
        print("\n" + "=" * 70)
        print(f"✗ E2SAR Back-to-Back Test FAILED: {e}")
        print("=" * 70)

        # Print consumer output if available
        if consumer_process is not None:
            print("\nConsumer output:")
            consumer_output = stop_consumer(consumer_process)
            print(consumer_output)

        raise

    finally:
        # Ensure consumer is stopped
        if consumer_process is not None and consumer_process.poll() is None:
            print("\nCleaning up consumer process...")
            stop_consumer(consumer_process)


def test_e2sar_back_to_back():
    """Pytest entry point for the E2SAR back-to-back test"""
    # Check if E2SAR is available
    try:
        import e2sar_py
        from mpi4py import MPI
    except ImportError as e:
        import pytest
        pytest.skip(f"E2SAR dependencies not available: {e}")

    # Run the test
    run_back_to_back_test()


if __name__ == "__main__":
    try:
        success = run_back_to_back_test()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\nTest failed with exception: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
