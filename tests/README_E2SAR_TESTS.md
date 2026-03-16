# E2SAR Integration Tests

## Back-to-Back Test

The `test_e2sar_back_to_back.py` script tests the full E2SAR integration pipeline by running a producer and consumer in back-to-back mode (without actual EJFAT hardware).

### What It Tests

1. **Producer**: Generates 50 random events (10 batches of 5 events)
   - Creates 100x100 float32 arrays
   - Sets event_id to 42
   - Sends via E2SAR Segmenter to localhost

2. **Consumer**: Receives events via E2SAR Reassembler
   - Listens on localhost UDP port 10000
   - Deserializes HDF5 data
   - Writes to HDF5 files in `e2sar_b2b_output/`

3. **Verification**: Validates output files
   - Checks correct number of files (10)
   - Verifies data shape, dtype, and values
   - Confirms event IDs match

### Running the Test

#### As a standalone script:
```bash
python tests/test_e2sar_back_to_back.py
```

#### With pytest:
```bash
pytest tests/test_e2sar_back_to_back.py -v
```

#### Run all tests:
```bash
pytest tests/
```

### Expected Output

```
======================================================================
E2SAR Back-to-Back Integration Test
======================================================================
Starting consumer with 1 rank(s)...
Waiting 3s for consumer to initialize...
Consumer started successfully
Starting producer with 1 rank(s)...
Producer completed
Stopping consumer...
Consumer stopped gracefully

Verifying output files in e2sar_b2b_output...
Found 10 output files:
  b2b_test_r0_0.h5 (204.1 KB)
  ...
✓ Correct number of output files (10)

Verifying contents of 10 files...

✓ All files contain valid data:
  Array shape: (1, 5, 100, 100)
  Array dtype: float32
  Array range: [0.0000, 1.0000]
  Event ID: 42

======================================================================
✓ E2SAR Back-to-Back Test PASSED
======================================================================
```

### Requirements

- `e2sar_py` library
- `mpi4py`
- `h5py`
- `numpy`
- `lclstreamer` installed

The test will be skipped by pytest if E2SAR dependencies are not available.

### Configuration

The test uses configuration files from `examples/`:
- `examples/e2sar-back-to-back-producer.yaml`
- `examples/e2sar-back-to-back-consumer.yaml`

### Troubleshooting

#### Port Already In Use
If you see "Address already in use" errors, clean up any running processes:
```bash
killall -9 lclstreamer
killall -9 python3.13
```

#### macOS UDP Socket Issues
The test uses 1 MPI rank instead of 2 to avoid UDP socket binding issues on macOS. This is a platform-specific limitation and doesn't affect the core E2SAR functionality.

#### Consumer Timeout
If the consumer fails to start, check:
- E2SAR library is properly installed
- Ports 10000-10001 are available
- MPI is working correctly (`mpirun --version`)

### Output Files

Test output files are written to `e2sar_b2b_output/` and automatically cleaned up between test runs. Each file contains:
- `/data/array`: Shape (1, 5, 100, 100), dtype float32
- `/data/event_id`: Shape (1, 5), all values = 42
