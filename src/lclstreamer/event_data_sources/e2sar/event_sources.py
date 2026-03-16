import os
import pickle
import sys
from collections.abc import Generator
from io import BytesIO

import h5py

try:
    import e2sar_py
    from mpi4py import MPI
except ImportError as e:
    print(f"Warning: E2SAR dependencies not available: {e}", file=sys.stderr)
    e2sar_py = None
    MPI = None

from stream.core import source

from ...models.parameters import (
    DataSourceParameters,
    E2SAREventSourceParameters,
)
from ...utils.logging import log
from ...utils.protocols import EventSourceProtocol
from ...utils.typing import StrFloatIntNDArray


class E2SAREventSource(EventSourceProtocol):
    """
    Event source that receives events via EJFAT transport using E2SAR Reassembler

    This event source receives serialized event data through EJFAT load balancers
    and deserializes it back into the event dictionary format expected by the
    processing pipeline.
    """

    def __init__(
        self,
        parameters: E2SAREventSourceParameters,
        data_source_parameters: dict[str, DataSourceParameters],
        source_identifier: str,
        worker_pool_size: int,
        worker_rank: int,
    ) -> None:
        """
        Initializes an E2SAR Event Source

        This event source receives byte objects through EJFAT using the E2SAR
        Reassembler component and deserializes them into event dictionaries.
        Each MPI rank listens on a block of ports starting at
        (base_port + num_recv_threads * rank).

        Arguments:
            parameters: The event source configuration parameters
            data_source_parameters: Configuration for data sources (unused for E2SAR)
            source_identifier: Identifier for this data source (unused for E2SAR)
            worker_pool_size: The size of the worker pool
            worker_rank: The rank of the worker calling the function

        Raises:
            SystemExit: If E2SAR dependencies are not available or initialization fails
        """
        if e2sar_py is None or MPI is None:
            log.error("E2SAR dependencies (e2sar_py, mpi4py) are not available")
            sys.exit(1)

        if parameters.type != "E2SAREventSource":
            log.error("Event source parameters do not match the expected type")
            sys.exit(1)

        self._parameters = parameters
        self._rank = worker_rank
        self._pool_size = worker_pool_size

        # Each rank listens on a block of ports (one per receive thread)
        # Starting port = base + (num_threads * rank)
        self._listen_port = parameters.listen_port + (parameters.num_recv_threads * worker_rank)

        # Get EJFAT URI from parameter or environment
        ejfat_uri_str = parameters.ejfat_uri
        if ejfat_uri_str is None:
            ejfat_uri_str = os.environ.get("EJFAT_URI")
            if ejfat_uri_str is None:
                log.error("EJFAT_URI not provided in parameters or environment")
                sys.exit(1)

        # Parse EJFAT URI
        try:
            self._ejfat_uri = e2sar_py.EjfatURI(
                uri=ejfat_uri_str,
                tt=e2sar_py.EjfatURI.TokenType.instance
            )
        except Exception as e:
            log.error(f"Failed to parse EJFAT URI '{ejfat_uri_str}': {e}")
            sys.exit(1)

        # Get data address from URI
        try:
            data_addr_result = self._ejfat_uri.get_data_addr_v4()
            if data_addr_result.has_error():
                log.error(
                    f"Failed to get data address from EJFAT URI: "
                    f"{data_addr_result.error().message}"
                )
                sys.exit(1)
            # Extract IPAddress from result (returns tuple of (IPAddress, port))
            self._data_ip, _ = data_addr_result.value()
        except Exception as e:
            log.error(f"Failed to parse data IP from EJFAT URI: {e}")
            sys.exit(1)

        # Configure reassembler flags
        reas_flags = e2sar_py.DataPlane.Reassembler.ReassemblerFlags()
        reas_flags.useCP = parameters.use_control_plane
        reas_flags.withLBHeader = parameters.with_lb_header
        reas_flags.eventTimeout_ms = parameters.event_timeout_ms

        # Create reassembler
        try:
            self._reassembler = e2sar_py.DataPlane.Reassembler(
                self._ejfat_uri,
                self._data_ip,
                self._listen_port,
                parameters.num_recv_threads,
                reas_flags
            )
        except Exception as e:
            log.error(f"Failed to create E2SAR Reassembler: {e}")
            sys.exit(1)

        # Open and start the reassembler
        try:
            result = self._reassembler.OpenAndStart()
            if result.has_error():
                log.error(f"Failed to start E2SAR Reassembler: {result.error().message}")
                sys.exit(1)
        except Exception as e:
            log.error(f"Failed to open E2SAR Reassembler: {e}")
            sys.exit(1)

        log.info(
            f"E2SAR Event Source initialized on rank {self._rank} "
            f"(port={self._listen_port}, deserializer={parameters.deserializer_type})"
        )

        self._event_count = 0

    @source
    def get_events(self) -> Generator[dict[str, StrFloatIntNDArray | None]]:
        """
        Retrieves events from the E2SAR Reassembler

        Continuously receives events via EJFAT, deserializes them according to
        the configured deserializer type, and yields them as event dictionaries.

        Yields:
            data: A dictionary storing data for an event
        """
        while True:
            try:
                # Receive event with configured timeout
                recv_len, recv_bytes, recv_event_num, recv_data_id = (
                    self._reassembler.recvEventBytes(
                        wait_ms=self._parameters.event_timeout_ms
                    )
                )

                # Check for errors or timeouts
                if recv_len == -2:
                    # Fatal error
                    log.error(f"E2SAR Reassembler fatal error on rank {self._rank}")
                    break
                elif recv_len == -1:
                    # Timeout - no event received
                    continue
                elif recv_len == 0:
                    # Empty event
                    log.warning(f"E2SAR received empty event on rank {self._rank}")
                    continue

                # Successfully received event
                self._event_count += 1

                # Deserialize based on type
                try:
                    event_data = self._deserialize_event(recv_bytes)
                    yield event_data
                except Exception as e:
                    log.warning(
                        f"Failed to deserialize event {self._event_count} "
                        f"on rank {self._rank}: {e}"
                    )
                    continue

            except KeyboardInterrupt:
                log.info(f"E2SAR Event Source interrupted on rank {self._rank}")
                break
            except Exception as e:
                log.error(f"E2SAR Event Source error on rank {self._rank}: {e}")
                break

        log.info(
            f"E2SAR Event Source finished on rank {self._rank} "
            f"({self._event_count} events received)"
        )

    def _deserialize_event(
        self, data: bytes
    ) -> dict[str, StrFloatIntNDArray | None]:
        """
        Deserializes received bytes into an event dictionary

        Arguments:
            data: Raw bytes received from E2SAR Reassembler

        Returns:
            event_dict: Dictionary mapping data source names to arrays

        Raises:
            Exception: If deserialization fails
        """
        if self._parameters.deserializer_type == "hdf5":
            return self._deserialize_hdf5(data)
        elif self._parameters.deserializer_type == "pickle":
            return self._deserialize_pickle(data)
        elif self._parameters.deserializer_type == "raw":
            return {"raw_data": data}
        else:
            raise ValueError(
                f"Unknown deserializer type: {self._parameters.deserializer_type}"
            )

    def _deserialize_hdf5(
        self, data: bytes
    ) -> dict[str, StrFloatIntNDArray | None]:
        """
        Deserializes HDF5-formatted bytes into an event dictionary

        Arguments:
            data: HDF5-formatted bytes

        Returns:
            event_dict: Dictionary mapping dataset paths to arrays
        """
        event_dict: dict[str, StrFloatIntNDArray | None] = {}

        with BytesIO(data) as bio:
            with h5py.File(bio, 'r') as h5f:
                # Recursively extract all datasets from HDF5 file
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

    def _deserialize_pickle(
        self, data: bytes
    ) -> dict[str, StrFloatIntNDArray | None]:
        """
        Deserializes pickle-formatted bytes into an event dictionary

        Arguments:
            data: Pickle-formatted bytes

        Returns:
            event_dict: Dictionary unpickled from bytes
        """
        return pickle.loads(data)

    def close(self) -> None:
        """Explicitly close the reassembler and cleanup resources"""
        try:
            if hasattr(self, '_reassembler'):
                self._reassembler.stopThreads()
                log.info(f"E2SAR Event Source closed on rank {self._rank}")
        except Exception as e:
            log.warning(f"Error closing E2SAR Event Source: {e}")

    def __del__(self) -> None:
        """Cleanup on deletion"""
        self.close()
