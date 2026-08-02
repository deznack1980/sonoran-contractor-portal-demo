"""Builds connector instances for each 'connected' jurisdiction.

Adding a jurisdiction later:
  - Socrata/ArcGIS-Hub-shaped source: complete its jurisdictions.yaml entry
    (endpoint_url, resource_id, status: connected) and add its field_map
    here (or wherever the equivalent of MESA_FIELD_MAP lives).
  - New source shape entirely: add a BaseConnector subclass in this package
    and a branch in `build_connector` below.
"""

from pipeline.connectors.arcgis_hub import (
    ArcGISHubConnector,
    build_buckeye_connector,
    build_chandler_connector,
    build_gilbert_connector,
    build_goodyear_connector,
    build_peoria_connector,
    build_phoenix_connector,
    build_scottsdale_connector,
    build_tempe_connector,
)
from pipeline.connectors.base import ConnectorNotConfiguredError
from pipeline.connectors.socrata import build_mesa_connector

# Slug -> zero-arg factory. Only jurisdictions with a real, working connector
# implementation belong here, regardless of what jurisdictions.yaml says —
# the registry is the second gate (config says "should be connected",
# the registry says "here's working code for it").
_CONNECTOR_FACTORIES = {
    "mesa_az": build_mesa_connector,
    "tempe_az": build_tempe_connector,
    "gilbert_az": build_gilbert_connector,
    "scottsdale_az": build_scottsdale_connector,
    "chandler_az": build_chandler_connector,
    "peoria_az": build_peoria_connector,
    "goodyear_az": build_goodyear_connector,
    "phoenix_az": build_phoenix_connector,
    "buckeye_az": build_buckeye_connector,
}


def build_connector(slug: str, connector_type: str):
    if slug in _CONNECTOR_FACTORIES:
        return _CONNECTOR_FACTORIES[slug]()

    if connector_type == "arcgis_hub":
        # No verified endpoint yet for any ArcGIS Hub jurisdiction — this
        # deliberately raises rather than fabricating data.
        return ArcGISHubConnector(jurisdiction_slug=slug, service_url=None)

    raise ConnectorNotConfiguredError(
        f"No connector implementation registered for jurisdiction '{slug}' "
        f"(connector_type={connector_type!r})."
    )
