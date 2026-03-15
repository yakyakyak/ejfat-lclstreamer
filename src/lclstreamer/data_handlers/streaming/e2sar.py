import os
import sys

try:
    import e2sar_py
    from mpi4py import MPI
except ImportError as e:
    print(f"Warning: E2SAR dependencies not available: {e}", file=sys.stderr)
    e2sar_py = None
    MPI = None

from ...models.parameters import E2SARDataHandlerParameters
from ...utils.logging import log
from ...utils.protocols import DataHandlerProtocol


class E2SARDataHandler(DataHandlerProtocol):
    """
    Data handler that sends events via EJFAT transport using E2SAR Segmenter

    This handler implements the DataHandlerProtocol to send serialized event
    data through EJFAT load balancers. Each MPI rank gets unique data_id and
    eventsrc_id values to enable parallel data transmission.
    """

    def __init__(self, parameters: E2SARDataHandlerParameters) -> None:
        """
        Initializes an E2SAR Data Handler

        This data handler sends byte objects through EJFAT using the E2SAR
        Segmenter component. It automatically derives unique IDs for each MPI
        rank if not explicitly provided.

        Arguments:
            parameters: The data handler configuration parameters

        Raises:
            SystemExit: If E2SAR dependencies are not available or initialization fails
        """
        if e2sar_py is None or MPI is None:
            log.error("E2SAR dependencies (e2sar_py, mpi4py) are not available")
            sys.exit(1)

        self._parameters = parameters

        # Get MPI rank for unique ID assignment
        self._comm = MPI.COMM_WORLD
        self._rank = self._comm.Get_rank()

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

        # Derive unique IDs from MPI rank if not provided
        if parameters.data_id is not None:
            self._data_id = parameters.data_id & 0xFFFF
        else:
            # Use rank offset to create unique 16-bit data_id
            self._data_id = (0x0500 + self._rank) & 0xFFFF

        if parameters.eventsrc_id is not None:
            self._eventsrc_id = parameters.eventsrc_id & 0xFFFFFFFF
        else:
            # Use rank in upper bits to create unique 32-bit eventsrc_id
            self._eventsrc_id = (0x10000000 | self._rank) & 0xFFFFFFFF

        # Configure segmenter flags
        seg_flags = e2sar_py.DataPlane.Segmenter.SegmenterFlags()
        seg_flags.useCP = parameters.use_control_plane
        seg_flags.syncPeriodMs = parameters.sync_period_ms
        seg_flags.rateGbps = parameters.rate_gbps
        seg_flags.mtu = parameters.mtu
        seg_flags.numSendSockets = parameters.num_send_sockets

        # Create segmenter
        try:
            self._segmenter = e2sar_py.DataPlane.Segmenter(
                self._ejfat_uri,
                self._data_id,
                self._eventsrc_id,
                seg_flags
            )
        except Exception as e:
            log.error(f"Failed to create E2SAR Segmenter: {e}")
            sys.exit(1)

        # Open and start the segmenter
        try:
            result = self._segmenter.OpenAndStart()
            if result.has_error():
                log.error(f"Failed to start E2SAR Segmenter: {result.error().message}")
                sys.exit(1)
        except Exception as e:
            log.error(f"Failed to open E2SAR Segmenter: {e}")
            sys.exit(1)

        log.info(
            f"E2SAR Data Handler initialized on rank {self._rank} "
            f"(data_id=0x{self._data_id:04x}, eventsrc_id=0x{self._eventsrc_id:08x})"
        )

    def __call__(self, data: bytes) -> None:
        """
        Sends a binary object through the EJFAT network via E2SAR Segmenter

        Arguments:
            data: A bytes object containing serialized event data
        """
        try:
            result = self._segmenter.sendEvent(data, len(data))
            if result.has_error():
                log.warning(
                    f"E2SAR send failed on rank {self._rank}: "
                    f"{result.error().message}"
                )
        except Exception as e:
            log.warning(f"E2SAR send exception on rank {self._rank}: {e}")

    def close(self) -> None:
        """Explicitly close the segmenter and cleanup resources"""
        try:
            if hasattr(self, '_segmenter'):
                self._segmenter.stopThreads()
                log.info(f"E2SAR Data Handler closed on rank {self._rank}")
        except Exception as e:
            log.warning(f"Error closing E2SAR Data Handler: {e}")

    def __del__(self) -> None:
        """Cleanup on deletion"""
        self.close()
