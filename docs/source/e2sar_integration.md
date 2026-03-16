# EJFAT/E2SAR Integration for LCLStreamer

This document describes the EJFAT (ESnet FPGA-Accelerated Transport) integration with LCLStreamer using the E2SAR library.

## Overview

The E2SAR integration adds two new components to LCLStreamer:

1. **E2SARDataHandler** - A data sink that sends serialized events via EJFAT transport
2. **E2SAREventSource** - An event source that receives events via EJFAT transport

This enables LCLStreamer to use EJFAT's hardware-accelerated load balancing for high-performance data streaming (100+ Gbps) as an alternative to the existing NNG-Stream transport.

## Architecture

```
Producer Pipeline:
[Psana/Internal] → [Pipeline] → [Serializer] → [E2SARDataHandler] → EJFAT/UDP
                                                         ↓
                                                  E2SAR Segmenter

                    EJFAT Network (FPGA Load Balancer)
                                   ↓
                             UDP Distribution

Consumer Pipeline:
EJFAT/UDP → [E2SAREventSource] → [Deserialize] → [Processing] → [Handlers]
                    ↓
             E2SAR Reassembler
```

## Components

### 1. E2SARDataHandler

**File:** `src/lclstreamer/data_handlers/streaming/e2sar.py`

**Purpose:** Sends serialized event data via EJFAT transport using E2SAR Segmenter.

**Key Features:**
- Automatic MPI rank-based ID assignment
- Hardware-accelerated load balancing via EJFAT
- Configurable rate limiting and MTU
- Lifecycle management (OpenAndStart/stopThreads)

**Configuration Parameters:**
```yaml
data_handlers:
  - type: E2SARDataHandler
    ejfat_uri: null              # EJFAT URI (or use EJFAT_URI env var)
    data_id: null                # 16-bit stream ID (auto from rank if null)
    eventsrc_id: null            # 32-bit source ID (auto from rank if null)
    use_control_plane: true      # Enable sync packets and LB registration
    rate_gbps: 10.0              # Rate limit (-1.0 = unlimited)
    mtu: 9000                    # MTU (9000 for jumbo frames)
    sync_period_ms: 1000         # Sync packet interval
    num_send_sockets: 4          # Sockets for LAG distribution
```

**MPI Rank Coordination:**
Each MPI rank automatically gets unique IDs:
- `data_id = (0x0500 + rank) & 0xFFFF`
- `eventsrc_id = (0x10000000 | rank) & 0xFFFFFFFF`

### 2. E2SAREventSource

**File:** `src/lclstreamer/event_data_sources/e2sar/event_sources.py`

**Purpose:** Receives events via EJFAT transport using E2SAR Reassembler.

**Key Features:**
- Implements EventSourceProtocol with @source decorator
- Multiple deserialization formats (HDF5, pickle, raw)
- Per-rank port assignment
- Event reassembly with configurable timeout

**Configuration Parameters:**
```yaml
event_source:
  type: E2SAREventSource
  ejfat_uri: null              # EJFAT URI (or use EJFAT_URI env var)
  listen_port: 10000           # Base port (rank N uses port + N)
  num_recv_threads: 4          # Receive threads per rank
  use_control_plane: true      # Register with LB
  with_lb_header: false        # True for back-to-back testing
  event_timeout_ms: 500        # Reassembly timeout
  deserializer_type: hdf5      # hdf5, pickle, or raw
```

**MPI Rank Port Assignment:**
Each rank listens on `listen_port + rank` to enable parallel reception.

## Installation

### Dependencies

Add to your pixi environment or requirements:

```bash
# E2SAR Python bindings (must be built from source)
e2sar_py

# MPI (usually already present for LCLStreamer)
mpi4py

# Data serialization (usually already present)
h5py
```

### Building E2SAR

E2SAR must be built from source. Assuming E2SAR is at `/Users/yak/Projects/E2SAR`:

```bash
cd /Users/yak/Projects/E2SAR
mkdir -p build && cd build
cmake .. -DBUILD_PYTHON_BINDINGS=ON
make -j$(nproc)

# Add to PYTHONPATH
export PYTHONPATH=/Users/yak/Projects/E2SAR/build/src/pybind:$PYTHONPATH
```

## Usage

### Basic Producer Example

```bash
# Set EJFAT URI
export EJFAT_URI="ejfat://user@lb-host:port/lb/1?sync=sync-addr:port&data=data-addr:port"

# Run with MPI for parallel sending
pixi run mpirun -n 4 lclstreamer --config examples/e2sar-producer.yaml
```

### Basic Consumer Example

```bash
# Set EJFAT URI
export EJFAT_URI="ejfat://user@lb-host:port/lb/1?sync=sync-addr:port&data=data-addr"

# Run with MPI for parallel receiving
pixi run mpirun -n 4 lclstreamer --config examples/e2sar-consumer.yaml
```

### Back-to-Back Testing (No EJFAT Hardware)

For testing without actual EJFAT infrastructure:

```bash
# Terminal 1: Start consumer first
pixi run mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-consumer.yaml

# Terminal 2: Start producer
pixi run mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-producer.yaml
```

**Important for back-to-back testing:**
- Producer: `use_control_plane: false`
- Consumer: `use_control_plane: false` AND `with_lb_header: true`
- Both use same localhost addresses

## EJFAT URI Format

```
ejfat://[user]@[lb-host]:[lb-port]/lb/[lb-id]?sync=[sync-host]:[sync-port]&data=[data-host]:[data-port]
```

**Components:**
- `user` - Username (can be arbitrary for testing)
- `lb-host:lb-port` - Load balancer control plane address
- `lb-id` - Load balancer instance ID
- `sync` - Sync/control packet address
- `data` - Data packet address (producer includes port, consumer omits port)

**Examples:**

Producer URI (includes data port):
```
ejfat://slac@lb.example.com:9876/lb/1?sync=192.168.1.10:12345&data=192.168.1.20:10000
```

Consumer URI (omits data port - uses listen_port parameter):
```
ejfat://slac@lb.example.com:9876/lb/1?sync=192.168.1.10:12345&data=192.168.1.20
```

## Configuration Examples

### Production SLAC → HPC Streaming

**Producer (SLAC):**
```yaml
event_source:
  type: Psana1EventSource

data_serializer:
  type: HDF5BinarySerializer
  compression: gzip
  compression_level: 3

data_handlers:
  - type: E2SARDataHandler
    use_control_plane: true
    rate_gbps: 10.0
    mtu: 9000
```

**Consumer (HPC):**
```yaml
event_source:
  type: E2SAREventSource
  listen_port: 10000
  num_recv_threads: 4
  deserializer_type: hdf5

data_handlers:
  - type: BinaryFileWritingDataHandler
    write_directory: /scratch/lcls_data
```

### High-Throughput Streaming

For maximum throughput:

```yaml
data_handlers:
  - type: E2SARDataHandler
    rate_gbps: -1.0              # Unlimited rate
    mtu: 9000                    # Jumbo frames
    num_send_sockets: 8          # More sockets for LAG
    sync_period_ms: 500          # More frequent sync
```

## Deserialization Formats

### HDF5 (Default)
```yaml
event_source:
  type: E2SAREventSource
  deserializer_type: hdf5
```
Deserializes HDF5-formatted bytes into dictionary mapping dataset paths to arrays.

### Pickle
```yaml
event_source:
  type: E2SAREventSource
  deserializer_type: pickle
```
Deserializes Python pickle format. Use with caution (security implications).

### Raw
```yaml
event_source:
  type: E2SAREventSource
  deserializer_type: raw
```
Returns raw bytes in dictionary key `raw_data`. Use for custom deserialization.

## Troubleshooting

### Import Errors

```
ImportError: No module named 'e2sar_py'
```

**Solution:** Ensure E2SAR is built with Python bindings and PYTHONPATH is set:
```bash
export PYTHONPATH=/path/to/E2SAR/build/src/pybind:$PYTHONPATH
```

### URI Parsing Errors

```
Failed to parse EJFAT URI
```

**Solution:** Check URI format:
- Producer data address includes port: `data=host:port`
- Consumer data address omits port: `data=host`
- All components properly URL-encoded

### Back-to-Back Testing Fails

```
No events received
```

**Solution:** Verify back-to-back configuration:
1. Consumer `with_lb_header: true`
2. Both `use_control_plane: false`
3. Producer data port matches consumer listen_port
4. Start consumer before producer

### Send/Receive Errors

```
E2SAR send failed: ...
```

**Solutions:**
- Check network connectivity
- Verify firewall rules allow UDP traffic
- Ensure MTU matches network capabilities
- Check EJFAT load balancer is running (if using control plane)

## Performance Tuning

### Network Optimization

```yaml
# Producer
mtu: 9000                    # Use jumbo frames if supported
num_send_sockets: 8          # Match network LAG configuration
rate_gbps: 50.0              # Set to match link capacity

# Consumer
num_recv_threads: 8          # Match CPU cores available
event_timeout_ms: 200        # Lower for low-latency networks
```

### MPI Configuration

```bash
# Use more ranks for higher throughput
mpirun -n 16 lclstreamer --config ...

# Bind to specific network interfaces
mpirun --mca btl_tcp_if_include eth0 -n 8 lclstreamer --config ...
```

## Monitoring

Check E2SAR statistics in logs:
- Data handler logs show rank, data_id, eventsrc_id on init
- Event source logs show events received count
- Send/receive errors logged as warnings

## Limitations

1. **E2SAR dependency:** Requires E2SAR built with Python bindings
2. **UDP-based:** No guaranteed delivery (use event sequence checking)
3. **MPI required:** Both components assume MPI environment
4. **Port management:** Each rank needs unique port for receiving

## References

- **E2SAR Repository:** `/Users/yak/Projects/E2SAR`
- **E2SAR Test Examples:** `/Users/yak/Projects/E2SAR/test/py_test/test_b2b_DP.py`
- **LCLStreamer GitHub:** https://github.com/slac-lcls/lclstreamer
- **EJFAT Project:** https://www.es.net/science-engagement/technology-development/fpga-based-network-platform/

## Integration with Existing LCLStream

E2SAR components integrate seamlessly with existing LCLStreamer infrastructure:

- **Drop-in replacement:** Use `E2SARDataHandler` instead of `BinaryDataStreamingDataHandler`
- **Compatible serialization:** Works with all existing serializers (HDF5, Simplon)
- **MPI parallel:** Leverages same MPI infrastructure as psana
- **Configuration-driven:** No code changes needed, only YAML config

## Future Enhancements

Potential improvements:
- Auto-discovery of EJFAT load balancers
- Built-in event sequence checking
- Statistics reporting (throughput, loss rate)
- Integration with LCLStream-API for job orchestration
- Support for EJFAT reliable transport mode
