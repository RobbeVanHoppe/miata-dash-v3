"""Background data sources that feed :class:`miata_dash.state.VehicleState`."""

from .base import DataSource
from .can_source import CanSource
from .gps_source import GpsSource

__all__ = ["DataSource", "CanSource", "GpsSource"]
