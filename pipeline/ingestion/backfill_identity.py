"""Backfill contractor/applicant identity from stored raw_source_json.

Used when a connector mapping is improved after records were already ingested
— so reports and contractor profiles can pick up names without a full re-pull.
Never invents values; only copies fields already present in the raw JSON.
"""

from __future__ import annotations

import sqlite3

from pipeline.db.database import now_iso

# Same company-token idea as report_templates / phoenix connector — kept as
# SQL LIKEs so we can run bulk updates without loading every raw blob.
_COMPANY_SQL = """
(
     upper(coalesce({expr}, '')) LIKE '%LLC%'
  OR upper(coalesce({expr}, '')) LIKE '%INC%'
  OR upper(coalesce({expr}, '')) LIKE '%CORP%'
  OR upper(coalesce({expr}, '')) LIKE '%COMPANY%'
  OR upper(coalesce({expr}, '')) LIKE '%HOMES%'
  OR upper(coalesce({expr}, '')) LIKE '%BUILDER%'
  OR upper(coalesce({expr}, '')) LIKE '%CONSTRUCTION%'
  OR upper(coalesce({expr}, '')) LIKE '%CONTRACT%'
  OR upper(coalesce({expr}, '')) LIKE '%PLUMBING%'
  OR upper(coalesce({expr}, '')) LIKE '%ELECTRIC%'
  OR upper(coalesce({expr}, '')) LIKE '%DEVELOPMENT%'
  OR upper(coalesce({expr}, '')) LIKE '%PROPERT%'
  OR upper(coalesce({expr}, '')) LIKE '%HOLDINGS%'
)
"""


def backfill_identity_fields(conn: sqlite3.Connection) -> dict[str, int]:
    """Return counts of rows updated per jurisdiction/rule."""
    now = now_iso()
    updated: dict[str, int] = {}

    # Mesa: prefer contractor_name, else applicant (matches current field_map).
    cur = conn.execute(
        """
        UPDATE permits
        SET general_contractor_name = COALESCE(
                NULLIF(trim(json_extract(raw_source_json, '$.contractor_name')), ''),
                NULLIF(trim(json_extract(raw_source_json, '$.applicant')), '')
            ),
            last_updated_at = ?
        WHERE jurisdiction = 'mesa_az'
          AND (general_contractor_name IS NULL OR trim(general_contractor_name) = '')
          AND (
                NULLIF(trim(json_extract(raw_source_json, '$.contractor_name')), '') IS NOT NULL
             OR NULLIF(trim(json_extract(raw_source_json, '$.applicant')), '') IS NOT NULL
          )
        """,
        (now,),
    )
    updated["mesa_az_applicant_or_contractor"] = cur.rowcount

    # Tempe: ContractorCompanyName is often blank; company-like ProjectName
    # (e.g. "J PIERCE INVESTMENTS LLC") is the best published party string.
    tempe_project = "json_extract(raw_source_json, '$.ProjectName')"
    cur = conn.execute(
        f"""
        UPDATE permits
        SET general_contractor_name = NULLIF(trim({tempe_project}), ''),
            last_updated_at = ?
        WHERE jurisdiction = 'tempe_az'
          AND (general_contractor_name IS NULL OR trim(general_contractor_name) = '')
          AND NULLIF(trim(json_extract(raw_source_json, '$.ContractorCompanyName')), '') IS NULL
          AND NULLIF(trim({tempe_project}), '') IS NOT NULL
          AND {_COMPANY_SQL.format(expr=tempe_project)}
        """,
        (now,),
    )
    updated["tempe_az_projectname_company"] = cur.rowcount

    # Phoenix GC: PROFESS_NAME, else company-like PERMIT_NAME.
    phoenix_profess = "json_extract(raw_source_json, '$.PROFESS_NAME')"
    phoenix_permit = "json_extract(raw_source_json, '$.PERMIT_NAME')"
    cur = conn.execute(
        f"""
        UPDATE permits
        SET general_contractor_name = COALESCE(
                NULLIF(trim({phoenix_profess}), ''),
                CASE WHEN {_COMPANY_SQL.format(expr=phoenix_permit)}
                     THEN NULLIF(trim({phoenix_permit}), '')
                     ELSE NULL END
            ),
            last_updated_at = ?
        WHERE jurisdiction = 'phoenix_az'
          AND (general_contractor_name IS NULL OR trim(general_contractor_name) = '')
          AND (
                NULLIF(trim({phoenix_profess}), '') IS NOT NULL
             OR {_COMPANY_SQL.format(expr=phoenix_permit)}
          )
        """,
        (now,),
    )
    updated["phoenix_az_profess_or_permit_name"] = cur.rowcount

    # Phoenix project label: prefer human PERMIT_NAME over opaque PROJECT ids.
    cur = conn.execute(
        f"""
        UPDATE permits
        SET project_description = NULLIF(trim({phoenix_permit}), ''),
            last_updated_at = ?
        WHERE jurisdiction = 'phoenix_az'
          AND NULLIF(trim({phoenix_permit}), '') IS NOT NULL
          AND (
                project_description IS NULL
             OR trim(project_description) = ''
             OR trim(project_description) GLOB '[0-9]*'
             OR instr(trim(project_description), '-') > 0
                AND length(trim(project_description)) <= 12
          )
        """,
        (now,),
    )
    updated["phoenix_az_permit_name_as_project"] = cur.rowcount

    conn.commit()
    return updated
