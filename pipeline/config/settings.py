"""Paths and shared constants for the CorridorIQ pipeline."""

import os
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PIPELINE_DIR.parent

DB_PATH = PIPELINE_DIR / "db" / "corridoriq.db"
SCHEMA_PATH = PIPELINE_DIR / "db" / "schema.sql"
JURISDICTIONS_YAML = PIPELINE_DIR / "config" / "jurisdictions.yaml"

DATA_EXPORTS_DIR = PROJECT_ROOT / "data" / "exports"
REPORTS_GENERATED_DIR = PROJECT_ROOT / "reports" / "generated"
# Timestamped logs for the automated morning refresh (Sprint 6.3).
MORNING_REFRESH_LOG_DIR = PROJECT_ROOT / "logs" / "morning_refresh"
EXTERNAL_INTELLIGENCE_REFRESH_ENABLED = os.getenv(
    "CORRIDORIQ_EXTERNAL_REFRESH", "1").strip().lower() not in {"0", "false", "no", "off"}

# Analysis engine version tag, stored on every `projects` row so a future
# swap to real LLM classification is auditable (which rows used which logic).
ANALYSIS_VERSION = "rule-engine-v1"

# Minimum opportunity score (0–100) for a permit to appear in ranked
# sales reports. Every qualifying permit is included — no quantity cap.
REPORT_MIN_OPPORTUNITY_SCORE = 60

# Priority badge bands for executive reports (inclusive score ranges).
# Order matters: first matching band wins (highest first).
REPORT_PRIORITY_BANDS = (
    {"name": "Platinum", "min": 95, "max": 100},
    {"name": "Gold", "min": 90, "max": 94},
    {"name": "Silver", "min": 80, "max": 89},
    {"name": "Bronze", "min": 70, "max": 79},
    {"name": "Watch", "min": 60, "max": 69},
)

# Deprecated: reports no longer truncate by count. Kept as None so any
# leftover import that still references REPORT_TOP_N does not re-impose a
# silent 50-row cap. Prefer REPORT_MIN_OPPORTUNITY_SCORE instead.
REPORT_TOP_N = None

# ---------------------------------------------------------------
# Municipal Knowledge Engine — review triage (Sprint 2.1)
# All knobs below affect prioritization / gating only, never the
# opportunity-score weights or the 60 publish threshold.
# ---------------------------------------------------------------

# Relative weights for the review priority score (must sum to ~1.0).
KNOWLEDGE_PRIORITY_WEIGHTS = {
    "recent_volume": 0.30,   # occurrences in the last 30 days
    "total_volume": 0.20,    # total affected permits / occurrences
    "active_permits": 0.20,  # affected permits in the active window
    "status_impact": 0.15,   # affects status normalization
    "category_impact": 0.10, # affects permit-type / trade classification
    "high_score_impact": 0.05,  # avg opportunity score of affected permits
}

# Log-normalization caps for volume factors (value at/above cap -> 100).
KNOWLEDGE_PRIORITY_CAPS = {
    "recent_volume": 500,
    "total_volume": 5000,
    "active_permits": 3000,
}

# Priority tiers by score (first match wins, highest first).
KNOWLEDGE_PRIORITY_TIERS = (
    {"name": "Critical", "min": 70},
    {"name": "High", "min": 45},
    {"name": "Medium", "min": 20},
    {"name": "Low", "min": 0},
)

# Mapping confidence tiers (0–100).
KNOWLEDGE_CONFIDENCE_TIERS = (
    {"name": "Verified", "min": 95},
    {"name": "High", "min": 80},
    {"name": "Moderate", "min": 60},
    {"name": "Low", "min": 0},
)

# Allowed provenance for a dictionary mapping.
KNOWLEDGE_MAPPING_SOURCES = (
    "Source documentation",
    "Municipality-specific rule",
    "Description analysis",
    "Historical pattern",
    "Human judgment",
    "Bootstrap rule",
)

# Mappings below this confidence stay in 'draft' and do NOT affect production
# scoring unless a reviewer explicitly activates them.
KNOWLEDGE_MIN_ACTIVE_CONFIDENCE = 60

# Minimum confidence required to include an item in a batch approval.
KNOWLEDGE_BATCH_MIN_CONFIDENCE = 80

# "Active" permit window (days) for affected-active-permit counting.
KNOWLEDGE_ACTIVE_WINDOW_DAYS = 180

# HTTP politeness for external open-data APIs.
HTTP_USER_AGENT = "CorridorIQ-DataPipeline/0.1 (construction procurement intelligence; contact via corridoriq site)"
HTTP_REQUEST_DELAY_SECONDS = 0.25
SOCRATA_PAGE_SIZE = 1000

# ---------------------------------------------------------------
# Company Intelligence Foundation (Sprint 4)
# The company priority score is SEPARATE from permit opportunity
# scoring — it never touches the 40/25/20/15 weights or the 60
# publish threshold. It ranks companies for sales attention using
# verified permit/project activity only.
# ---------------------------------------------------------------
COMPANY_METRICS_MODEL_VERSION = "company-metrics-v1"

# Confidence bands for deterministic identity resolution (0–100).
#   95–100 automatic exact match
#   80–94  probable match (review only if conflicting fields)
#   60–79  possible duplicate -> review queue
#   <60    create a separate company
COMPANY_MATCH_AUTO_MIN = 95
COMPANY_MATCH_PROBABLE_MIN = 80
COMPANY_MATCH_REVIEW_MIN = 60

# Per-signal confidence contributions for the matcher (points, capped at 100).
COMPANY_MATCH_SIGNALS = {
    "license_exact": 100,       # same contractor license number
    "source_id_exact": 100,     # same source_system + source_record_id
    "name_exact": 85,           # identical normalized name
    "alias_exact": 82,          # matches a preserved alias
    "name_plus_city": 92,       # normalized name + same city
    "name_plus_phone": 96,      # normalized name + same phone
    "name_plus_license_state": 90,
    "phone_exact": 70,          # same phone, different name
    "email_domain": 55,         # shared email domain (weak alone)
}

# Fields that, when they disagree between the source and a candidate, demote an
# otherwise-probable match into the review queue. City/state are intentionally
# NOT conflicts: a contractor working across multiple cities is the same company
# expanding (the core cross-jurisdiction premise), not a different entity. Only a
# differing license number or phone is treated as a genuine identity conflict.
COMPANY_MATCH_CONFLICT_FIELDS = ("license_number", "main_phone")

# Company priority score weights (must sum to ~1.0). Verified-activity only.
COMPANY_PRIORITY_WEIGHTS = {
    "recent_activity": 0.30,     # projects in the last 30 days
    "project_volume": 0.20,      # total project count
    "avg_opportunity": 0.20,     # average permit opportunity score
    "commercial_mix": 0.10,      # commercial share of projects
    "geographic_footprint": 0.10,  # distinct municipalities
    "activity_growth": 0.10,     # 90d growth trend
}

# Log-normalization caps for company volume factors (value at/above -> 100).
COMPANY_PRIORITY_CAPS = {
    "recent_activity": 25,       # projects in last 30d
    "project_volume": 300,       # total projects
    "geographic_footprint": 9,   # municipalities (max connected markets)
}

# Company priority tiers by score (first match wins, highest first).
COMPANY_PRIORITY_TIERS = (
    {"name": "Critical", "min": 75},
    {"name": "High", "min": 50},
    {"name": "Medium", "min": 25},
    {"name": "Low", "min": 0},
)

# A company/project counts as "active" if its earliest opportunity date is
# within this many days (mirrors lifecycle recency, not the permit score).
COMPANY_ACTIVE_WINDOW_DAYS = 180

# Backfill chunk size (permits processed per transaction; resumable).
COMPANY_BACKFILL_CHUNK = 5000

# Company Intelligence API + static export.
COMPANY_API_PORT = 8770
COMPANY_PAGE_SIZE_DEFAULT = 50
COMPANY_PAGE_SIZE_MAX = 200

# ---------------------------------------------------------------
# Secure Employee CRM (Sprint 5) — auth, sessions, RBAC.
# No secrets/passwords are stored here. Production flips the Secure
# cookie flag on via the CORRIDORIQ_ENV=production environment variable.
# ---------------------------------------------------------------
import os as _os

SALES_API_PORT = 8780
SESSION_COOKIE_NAME = "corridoriq_session"
SESSION_TTL_HOURS = 12
AUTH_MAX_FAILED_LOGINS = 5
AUTH_LOCKOUT_MINUTES = 15
# Secure cookies + HTTPS assumptions only in production.
AUTH_PRODUCTION = _os.environ.get("CORRIDORIQ_ENV", "").lower() == "production"
CRM_PAGE_SIZE_DEFAULT = 50
CRM_PAGE_SIZE_MAX = 200

# ---------------------------------------------------------------
# Multi-Supplier Product Pricing (Sprint 6)
# "Cheapest" = lowest estimated TOTAL fulfillment cost, not lowest
# unit price. Delivery uses a variable per-mile cost only for V1 —
# the fixed monthly truck cost is informational and NOT allocated
# to individual deliveries yet. All values below are defaults; an
# admin can override them at runtime via the pricing_config table.
# ---------------------------------------------------------------
# CorridorIQ-managed delivery pilot assumptions.
TRUCK_MONTHLY_FIXED_COST = 1100.0   # informational only (not allocated per delivery)
DELIVERY_PER_MILE = 0.21            # variable mileage cost per mile
DELIVERY_BASE_FEE = 0.0             # admin-configurable flat base fee per delivery
DELIVERY_MIN_CHARGE = 0.0           # optional minimum delivery charge (0 = none)
DELIVERY_MARKUP_PCT = 0.0           # admin-configurable markup on delivery cost (%)
DELIVERY_HANDLING_COST = 0.0        # per-order handling; default $0
# Miles are estimated by great-circle distance × this road-winding factor.
DELIVERY_ROAD_FACTOR = 1.25
# Fallback when neither warehouse nor jobsite coordinates are known.
DELIVERY_DEFAULT_MILES = 20.0

# Product search paging.
PRODUCT_PAGE_SIZE_DEFAULT = 50
PRODUCT_PAGE_SIZE_MAX = 200

# Default warehouse origin for the first supplier (Phoenix, AZ metro).
# Real coordinates can be set per-supplier via the admin UI.
SUPPLIER_DEFAULT_LAT = 33.4484
SUPPLIER_DEFAULT_LNG = -112.0740

# ---------------------------------------------------------------
# Automated Morning Data Pipeline (Sprint 6.3)
# One master run refreshes CorridorIQ before the business day.
# These knobs affect scheduling/retry/freshness only — never the
# opportunity-score weights or the 60-point publish threshold.
# ---------------------------------------------------------------
MORNING_REFRESH_RUN_TYPE = "morning_refresh"

# Transient network failures are retried with exponential backoff.
# Authentication / invalid-request failures are NOT retried.
MORNING_RETRY_MAX_ATTEMPTS = 3          # total attempts per jurisdiction fetch
MORNING_RETRY_BACKOFF_SECONDS = 2.0     # base; doubles each attempt (2s, 4s, ...)
MORNING_RETRY_BACKOFF_MAX_SECONDS = 30.0

# A 'running' pipeline_runs row older than this is treated as abandoned
# (crashed process) so a new run is not blocked forever.
MORNING_RUN_STALE_MINUTES = 120

# Data-freshness thresholds (days since the newest source record for a
# jurisdiction). Failed overrides these when the last sync errored.
MORNING_FRESHNESS_CURRENT_DAYS = 2      # <= this -> Current
MORNING_FRESHNESS_DELAYED_DAYS = 7      # <= this -> Delayed; beyond -> Stale

# Coarse AZ city centroids so a text jobsite ("Phoenix, AZ") can be routed in
# V1 without an external geocoder. Extend as needed.
CITY_CENTROIDS = {
    ("PHOENIX", "AZ"): (33.4484, -112.0740),
    ("MESA", "AZ"): (33.4152, -111.8315),
    ("SCOTTSDALE", "AZ"): (33.4942, -111.9261),
    ("TEMPE", "AZ"): (33.4255, -111.9400),
    ("CHANDLER", "AZ"): (33.3062, -111.8413),
    ("GILBERT", "AZ"): (33.3528, -111.7890),
    ("GLENDALE", "AZ"): (33.5387, -112.1860),
    ("PEORIA", "AZ"): (33.5806, -112.2374),
    ("SURPRISE", "AZ"): (33.6292, -112.3680),
    ("GOODYEAR", "AZ"): (33.4353, -112.3576),
    ("AVONDALE", "AZ"): (33.4356, -112.3496),
    ("BUCKEYE", "AZ"): (33.3703, -112.5838),
    ("TUCSON", "AZ"): (32.2226, -110.9747),
    ("FLAGSTAFF", "AZ"): (35.1983, -111.6513),
    ("QUEEN CREEK", "AZ"): (33.2487, -111.6343),
    ("CASA GRANDE", "AZ"): (32.8795, -111.7574),
}
