"""Generic Socrata (SODA API) connector.

Used for Mesa today. Any future jurisdiction whose open-data portal runs on
Socrata just needs a `field_map` entry — no new code.
"""

import os
import time
from datetime import datetime
from typing import Callable, Iterable, Optional, Union

import requests

from pipeline.config.settings import HTTP_REQUEST_DELAY_SECONDS, HTTP_USER_AGENT, SOCRATA_PAGE_SIZE
from pipeline.connectors.base import BaseConnector, explicit_contractor_name


# field_map values are either:
#   - a string: source column name, copied as-is
#   - a callable: (raw_dict) -> mapped_value, for anything needing transform
FieldMapEntry = Union[str, Callable[[dict], object]]


class SocrataConnector(BaseConnector):
    def __init__(
        self,
        jurisdiction_slug: str,
        domain: str,
        resource_id: str,
        field_map: dict,
        date_field: Optional[str] = None,
        city_name: Optional[str] = None,
    ):
        super().__init__(jurisdiction_slug)
        self.domain = domain.rstrip("/")
        self.resource_id = resource_id
        self.field_map = field_map
        self.date_field = date_field
        self.city_name = city_name
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": HTTP_USER_AGENT})
        app_token = os.environ.get("SOCRATA_APP_TOKEN")
        if app_token:
            self.session.headers["X-App-Token"] = app_token

    @property
    def resource_url(self) -> str:
        return f"{self.domain}/resource/{self.resource_id}.json"

    def fetch_raw(self, since: Optional[datetime] = None) -> Iterable[dict]:
        offset = 0
        where_clause = None
        if since and self.date_field:
            since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
            where_clause = f"{self.date_field} >= '{since_str}'"

        while True:
            params = {"$limit": SOCRATA_PAGE_SIZE, "$offset": offset}
            if where_clause:
                params["$where"] = where_clause

            response = self.session.get(self.resource_url, params=params, timeout=30)
            response.raise_for_status()
            page = response.json()

            if not page:
                break

            for record in page:
                yield record

            if len(page) < SOCRATA_PAGE_SIZE:
                break

            offset += SOCRATA_PAGE_SIZE
            time.sleep(HTTP_REQUEST_DELAY_SECONDS)

    def map_record(self, raw: dict) -> dict:
        mapped = self._empty_permit_dict()
        for target_field, source in self.field_map.items():
            if callable(source):
                mapped[target_field] = source(raw)
            else:
                mapped[target_field] = raw.get(source)
        if self.city_name and not mapped.get("city"):
            mapped["city"] = self.city_name
        return mapped


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------
# Mesa, AZ — verified live dataset (data.mesaaz.gov, resource m2kk-w2hz)
# ------------------------------------------------------------------
MESA_FIELD_MAP = {
    "permit_number": "permit_number",
    "permit_type": "permit_type",
    "status": "status",
    "description": "description_of_work",
    "project_description": "description_of_work",
    "issued_date": "issued_date",
    "finaled_date": "finaled_date",
    "job_address": "property_address",
    "city": lambda raw: "Mesa",
    "parcel_number": "parcel_number",
    "valuation": lambda raw: _to_float(raw.get("icc_value") or raw.get("job_value")),
    "square_footage": lambda raw: _to_float(raw.get("total_square_feet")),
    "latitude": lambda raw: _to_float(raw.get("latitude")),
    "longitude": lambda raw: _to_float(raw.get("longitude")),
    # Prefer licensed contractor_name; when the city only published an
    # applicant (common on recent Mesa rows), use that rather than leaving
    # the outreach field blank — never invent a name that isn't in the source.
    "general_contractor_name": lambda raw: explicit_contractor_name(raw.get("contractor_name")),
    "applicant_name": lambda raw: (raw.get("applicant") or "").strip() or None,
    "contractor_source_role": lambda raw: "contractor" if explicit_contractor_name(raw.get("contractor_name")) else None,
    "contractor_source_field": lambda raw: "contractor_name" if explicit_contractor_name(raw.get("contractor_name")) else None,
    "contractor_evidence_confidence": lambda raw: 1.0 if explicit_contractor_name(raw.get("contractor_name")) else None,
    "contractor_verification_status": lambda raw: "verified" if explicit_contractor_name(raw.get("contractor_name")) else "unverified",
    "lead_type": lambda raw: "verified_contractor" if explicit_contractor_name(raw.get("contractor_name")) else "unverified_permit_contact",
    "why_this_lead": lambda raw: "Mesa contractor_name is explicit contractor evidence" if explicit_contractor_name(raw.get("contractor_name")) else "Mesa publishes no usable explicit contractor identity",
    "permit_url": lambda raw: None,  # Mesa's SODA API doesn't expose a per-record URL
}


def build_mesa_connector() -> SocrataConnector:
    return SocrataConnector(
        jurisdiction_slug="mesa_az",
        domain="https://data.mesaaz.gov",
        resource_id="m2kk-w2hz",
        field_map=MESA_FIELD_MAP,
        date_field="issued_date",
        city_name="Mesa",
    )
