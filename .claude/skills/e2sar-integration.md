---
skill: e2sar-integration
description: EJFAT/E2SAR integration patterns for LCLStreamer and similar high-performance data streaming frameworks. Covers E2SAR Segmenter/Reassembler usage, MPI coordination, and YAML-based configuration.
triggers:
  - e2sar
  - ejfat
  - lclstreamer
  - e2sar integration
  - ejfat transport
---

# EJFAT/E2SAR Integration Patterns

This skill provides reference patterns for integrating EJFAT (ESnet FPGA-Accelerated Transport) using the E2SAR library with high-performance data streaming frameworks.

## Overview

E2SAR integration enables:
- Hardware-accelerated load balancing (100+ Gbps)
- UDP-based transport with FPGA routing
- MPI-coordinated parallel data streams
- Event segmentation and reassembly

## Architecture

```
Producer: [Source] → [Pipeline] → [Serializer] → [E2SARDataHandler] → EJFAT/UDP
                                                         ↓
                                                  E2SAR Segmenter

                    EJFAT Network (FPGA Load Balancer)
                                   ↓

Consumer: EJFAT/UDP → [E2SAREventSource] → [Deserialize] → [Pipeline]
                              ↓
                       E2SAR Reassembler
```

## Component 1: E2SAR Segmenter (Data Handler)

### Implementation Pattern

```python
import e2sar_py
from mpi4py import MPI

class E2SARDataHandler:
    def __init__(self, parameters):
        # MPI rank coordination
        self._comm = MPI.COMM_WORLD
        self._rank = self._comm.Get_rank()

        # Parse EJFAT URI
        self._ejfat_uri = e2sar_py.EjfatURI(
            uri=parameters.ejfat_uri or os.environ["EJFAT_URI"],
            tt=e2sar_py.EjfatURI.TokenType.instance
        )

        # Auto-derive unique IDs from MPI rank
        self._data_id = (0x0500 + self._rank) & 0xFFFF  # 16-bit stream ID
        self._eventsrc_id = (0x10000000 | self._rank) & 0xFFFFFFFF  # 32-bit source ID

        # Configure segmenter
        seg_flags = e2sar_py.DataPlane.Segmenter.SegmenterFlags()
        seg_flags.useCP = parameters.use_control_plane
        seg_flags.syncPeriodMs = parameters.sync_period_ms
        seg_flags.rateGbps = parameters.rate_gbps
        seg_flags.mtu = parameters.mtu
        seg_flags.numSendSockets = parameters.num_send_sockets

        # Create and start segmenter
        self._segmenter = e2sar_py.DataPlane.Segmenter(
            self._ejfat_uri,
            self._data_id,
            self._eventsrc_id,
            seg_flags
        )

        result = self._segmenter.OpenAndStart()
        if result.has_error():
            raise RuntimeError(f"Failed to start segmenter: {result.error().message}")

    def __call__(self, data: bytes):
        """Send event data via EJFAT"""
        result = self._segmenter.sendEvent(data, len(data))
        if result.has_error():
            log.warning(f"Send failed: {result.error().message}")

    def close(self):
        """Cleanup"""
        self._segmenter.stopThreads()
```

### Pydantic Configuration Model

```python
from pydantic import BaseModel
from typing import Literal

class E2SARDataHandlerParameters(BaseModel):
    type: Literal["E2SARDataHandler"]
    ejfat_uri: str | None = None  # EJFAT URI or use env var
    data_id: int | None = None  # Auto from rank if None
    eventsrc_id: int | None = None  # Auto from rank if None
    use_control_plane: bool = True  # Enable sync & LB registration
    rate_gbps: float = -1.0  # -1.0 = unlimited
    mtu: int = 9000  # MTU for jumbo frames
    sync_period_ms: int = 1000  # Sync packet interval
    num_send_sockets: int = 4  # Sockets for LAG
```

### YAML Configuration

```yaml
data_handlers:
  - type: E2SARDataHandler
    ejfat_uri: null  # Read from EJFAT_URI env var
    data_id: null  # Auto-derive from MPI rank (0x0500 + rank)
    eventsrc_id: null  # Auto-derive from MPI rank (0x10000000 | rank)
    use_control_plane: true  # Enable sync packets and LB registration
    rate_gbps: 10.0  # Rate limit (use -1.0 for unlimited)
    mtu: 9000  # MTU for jumbo frames
    sync_period_ms: 1000  # Sync packet interval
    num_send_sockets: 4  # Number of send sockets for LAG
```

## Component 2: E2SAR Reassembler (Event Source)

### Implementation Pattern

```python
import e2sar_py
from mpi4py import MPI
from stream.core import source
from collections.abc import Generator

class E2SAREventSource:
    def __init__(self, parameters, worker_rank, worker_pool_size):
        self._rank = worker_rank
        self._pool_size = worker_pool_size

        # Parse EJFAT URI
        self._ejfat_uri = e2sar_py.EjfatURI(
            uri=parameters.ejfat_uri or os.environ["EJFAT_URI"],
            tt=e2sar_py.EjfatURI.TokenType.instance
        )

        # Get data IP from URI
        data_addr_result = self._ejfat_uri.get_data_addr_v4()
        if data_addr_result.has_error():
            raise RuntimeError(f"Failed to get data address: {data_addr_result.error().message}")
        self._data_ip, _ = data_addr_result.value()

        # Per-rank port assignment (critical for MPI parallel reception)
        # IMPORTANT: Each rank with num_recv_threads threads consumes that many ports
        # Formula: base_port + (num_recv_threads * rank)
        self._listen_port = parameters.listen_port + (parameters.num_recv_threads * worker_rank)

        # Configure reassembler
        reas_flags = e2sar_py.DataPlane.Reassembler.ReassemblerFlags()
        reas_flags.useCP = parameters.use_control_plane
        reas_flags.withLBHeader = parameters.with_lb_header
        reas_flags.eventTimeout_ms = parameters.event_timeout_ms

        # Create and start reassembler
        self._reassembler = e2sar_py.DataPlane.Reassembler(
            self._ejfat_uri,
            self._data_ip,
            self._listen_port,
            parameters.num_recv_threads,
            reas_flags
        )

        result = self._reassembler.OpenAndStart()
        if result.has_error():
            raise RuntimeError(f"Failed to start reassembler: {result.error().message}")

    @source
    def get_events(self) -> Generator[dict, None, None]:
        """Receive events via EJFAT"""
        while True:
            # Receive event
            recv_len, recv_bytes, recv_event_num, recv_data_id = (
                self._reassembler.recvEventBytes(
                    wait_ms=self._parameters.event_timeout_ms
                )
            )

            # Check for errors
            if recv_len == -2:  # Fatal error
                break
            elif recv_len == -1:  # Timeout
                continue
            elif recv_len == 0:  # Empty event
                continue

            # Deserialize and yield
            event_data = self._deserialize_event(recv_bytes)
            yield event_data

    def close(self):
        """Cleanup"""
        self._reassembler.stopThreads()
```

### Pydantic Configuration Model

```python
class E2SAREventSourceParameters(BaseModel):
    type: Literal["E2SAREventSource"]
    ejfat_uri: str | None = None  # EJFAT URI or use env var
    listen_port: int = 10000  # Base port (rank N uses port + num_threads*N)
    num_recv_threads: int = 4  # Receive threads per rank
    use_control_plane: bool = True  # Register with LB
    with_lb_header: bool = False  # True for back-to-back testing
    event_timeout_ms: int = 500  # Reassembly timeout
    deserializer_type: Literal["hdf5", "pickle", "raw"] = "hdf5"
```

### YAML Configuration

```yaml
event_source:
  type: E2SAREventSource
  ejfat_uri: null  # Read from EJFAT_URI env var
  listen_port: 10000  # Base port (rank N uses ports base + num_threads*N through base + num_threads*(N+1)-1)
  num_recv_threads: 4  # Receive threads per rank (each rank consumes this many ports)
  use_control_plane: true  # Register with load balancer
  with_lb_header: false  # Set true for back-to-back testing
  event_timeout_ms: 500  # Reassembly timeout
  deserializer_type: hdf5  # hdf5, pickle, or raw
```

## MPI Coordination Patterns

### Port Assignment with Multiple Threads

**Critical**: When using multiple receive threads, each rank needs a contiguous block of ports.

```python
# CORRECT formula for multi-threaded ranks:
listen_port = base_port + (num_recv_threads * rank)

# Example with base_port=10000, num_recv_threads=4:
# Rank 0: ports 10000-10003
# Rank 1: ports 10004-10007
# Rank 2: ports 10008-10011
```

### Unique ID Assignment

```python
# Each sender rank needs unique data_id and eventsrc_id
data_id = (0x0500 + rank) & 0xFFFF  # 16-bit stream ID
eventsrc_id = (0x10000000 | rank) & 0xFFFFFFFF  # 32-bit source ID
```

## EJFAT URI Formats

### Production (with EJFAT hardware)

```bash
# Producer URI (sends to LB)
export EJFAT_URI="ejfat://user@lb-host:19522/lb/1?sync=lb-host:19523&data=lb-host:19524"

# Consumer URI (registers with LB to receive)
export EJFAT_URI="ejfat://user@lb-host:19522/lb/1?sync=lb-host:19523&data=0.0.0.0"
```

### Back-to-Back Testing (without EJFAT hardware)

```bash
# Consumer and producer use same URI
export EJFAT_URI="ejfat://test@127.0.0.1:9876/lb/1?sync=127.0.0.1:12345&data=127.0.0.1"

# Important: Set with_lb_header=true in consumer config
```

## Deserialization Strategies

### HDF5 (Recommended)

```python
def _deserialize_hdf5(self, data: bytes) -> dict:
    event_dict = {}
    with BytesIO(data) as bio:
        with h5py.File(bio, 'r') as h5f:
            def extract_datasets(group, prefix=''):
                for key in group.keys():
                    item = group[key]
                    path = f"{prefix}/{key}" if prefix else key
                    if isinstance(item, h5py.Dataset):
                        event_dict[path] = item[()]
                    elif isinstance(item, h5py.Group):
                        extract_datasets(item, path)
            extract_datasets(h5f)
    return event_dict
```

### Pickle

```python
def _deserialize_pickle(self, data: bytes) -> dict:
    return pickle.loads(data)
```

### Raw

```python
def _deserialize_raw(self, data: bytes) -> dict:
    return {"raw_data": data}
```

## Error Handling Patterns

### Import Error Handling

```python
try:
    import e2sar_py
    from mpi4py import MPI
except ImportError as e:
    print(f"Warning: E2SAR dependencies not available: {e}", file=sys.stderr)
    e2sar_py = None
    MPI = None

# Later in __init__:
if e2sar_py is None or MPI is None:
    log.error("E2SAR dependencies (e2sar_py, mpi4py) are not available")
    sys.exit(1)
```

### Result Error Checking

```python
# E2SAR operations return Result objects
result = self._segmenter.OpenAndStart()
if result.has_error():
    log.error(f"Failed to start: {result.error().message}")
    sys.exit(1)

# Check send results
result = self._segmenter.sendEvent(data, len(data))
if result.has_error():
    log.warning(f"Send failed: {result.error().message}")
```

### Receive Error Codes

```python
recv_len, recv_bytes, _, _ = self._reassembler.recvEventBytes(wait_ms=timeout)

if recv_len == -2:
    # Fatal error - break loop
    log.error("Reassembler fatal error")
    break
elif recv_len == -1:
    # Timeout - no event received
    continue
elif recv_len == 0:
    # Empty event
    log.warning("Received empty event")
    continue
```

## Testing Patterns

### Back-to-Back Integration Test

```bash
# Terminal 1: Start consumer first
mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-consumer.yaml

# Terminal 2: Start producer (after consumer initializes)
mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-producer.yaml --num-events 100
```

### Automated Test Script Pattern

```python
def test_e2sar_back_to_back(num_ranks=2):
    # Step 1: Start consumer in background
    consumer_proc = subprocess.Popen([
        "mpirun", "-n", str(num_ranks),
        "lclstreamer", "--config", "consumer.yaml"
    ])

    # Step 2: Wait for initialization
    time.sleep(3)

    # Step 3: Run producer
    result = subprocess.run([
        "mpirun", "-n", str(num_ranks),
        "lclstreamer", "--config", "producer.yaml"
    ])

    # Step 4: Stop consumer
    consumer_proc.send_signal(signal.SIGINT)
    consumer_output, _ = consumer_proc.communicate(timeout=10)

    # Step 5: Validate output files
    output_files = list(Path("output_dir").glob("*.h5"))
    expected_files = batches_per_rank * num_ranks
    assert len(output_files) == expected_files
```

## Performance Tuning

### Throughput Optimization

```yaml
# High-throughput producer settings
data_handlers:
  - type: E2SARDataHandler
    rate_gbps: -1.0  # Unlimited
    mtu: 9000  # Jumbo frames
    num_send_sockets: 8  # More sockets for LAG
    sync_period_ms: 5000  # Less frequent sync
```

### Latency Optimization

```yaml
# Low-latency consumer settings
event_source:
  type: E2SAREventSource
  num_recv_threads: 8  # More threads
  event_timeout_ms: 100  # Shorter timeout
```

## Common Issues and Solutions

### Port Binding Conflicts (macOS)

**Issue**: Multiple MPI ranks can't bind to sequential ports on macOS.

**Solution**: Use formula `base_port + (num_threads * rank)` for proper port spacing.

### Connection Refused

**Issue**: Producer gets "Connection refused" errors.

**Solution**: Start consumer before producer in back-to-back mode.

### Missing Dependencies

**Issue**: `ImportError: No module named 'e2sar_py'`

**Solution**: E2SAR must be built from source and installed in environment.

## Integration Checklist

When adding E2SAR to a framework:

- [ ] Add e2sar_py and mpi4py dependencies
- [ ] Create Pydantic parameter models with discriminated unions
- [ ] Implement data handler with proper MPI rank coordination
- [ ] Implement event source with @source decorator
- [ ] Register components in setup.py with try/except
- [ ] Add YAML configuration examples
- [ ] Add back-to-back test configuration
- [ ] Document EJFAT URI formats
- [ ] Add automated integration test
- [ ] Handle import errors gracefully
- [ ] Check E2SAR result objects for errors
