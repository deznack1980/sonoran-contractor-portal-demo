"""CLI: import a supplier price catalog (CSV or XLSX).

    python -m pipeline.products.import_supplier_catalog --supplier sonoran \
        --file "C:\\path\\to\\export_catalog_product.csv"

Idempotent — rerunning updates existing offers instead of duplicating them.
Rows with price <= 0 are excluded and counted.
"""

from __future__ import annotations

import argparse

from pipeline.config import settings
from pipeline.db.database import init_db
from pipeline.products.importer import (
    SONORAN_DEFAULTS,
    import_catalog,
    read_rows_from_file,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import a supplier price catalog.")
    parser.add_argument("--supplier", required=True, help="Supplier code, e.g. sonoran")
    parser.add_argument("--file", required=True, help="Path to CSV or XLSX catalog")
    parser.add_argument("--profile", default=None,
                        help="Parser profile (default: auto for sonoran, else generic)")
    parser.add_argument("--name", default=None, help="Supplier display name")
    args = parser.parse_args(argv)

    supplier_code = args.supplier.strip().lower()
    profile = args.profile
    supplier_name = args.name
    location = None
    if supplier_code == "sonoran":
        profile = profile or SONORAN_DEFAULTS["profile"]
        supplier_name = supplier_name or SONORAN_DEFAULTS["supplier_name"]
        location = {"city": "Phoenix", "state": "AZ",
                    "latitude": settings.SUPPLIER_DEFAULT_LAT,
                    "longitude": settings.SUPPLIER_DEFAULT_LNG}
    profile = profile or "generic"

    print(f"Reading {args.file} ...")
    rows, headers = read_rows_from_file(args.file)
    print(f"  {len(rows)} rows, {len(headers)} columns; profile={profile}")

    conn = init_db()
    stats = import_catalog(
        conn, supplier_code=supplier_code, rows=rows, source_file=args.file,
        profile=profile, supplier_name=supplier_name, supplier_location=location,
    )
    conn.close()

    print("\nImport complete:")
    for k, v in stats.as_dict().items():
        print(f"  {k:>22}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
