"""Live ADOT advertisement and planholder ingestion.

The source is public, server-rendered HTML. Every opportunity and planholder is
retained with its source URL and retrieval timestamp. Only exact canonical
name/alias matches are linked automatically; ambiguous names remain unmatched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
import re
import sqlite3
from urllib.parse import urljoin

from pipeline.company_resolution.normalize import normalize_company_name


BASE_URL = "https://cnsads.azdot.gov"
CURRENT_URL = BASE_URL + "/current"
USER_AGENT = "CorridorIQ/1.0 (+public-record-ingestion; source=cnsads.azdot.gov)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value) -> str | None:
    value = " ".join(str(value or "").split()).strip()
    return value or None


def _date(value) -> str | None:
    value = _clean(value)
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_tbody = False
        self.row = None
        self.cell = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self.in_tbody = True
        elif self.in_tbody and tag == "tr":
            self.row = []
        elif self.row is not None and tag == "td":
            self.cell = {"text": [], "links": []}
        elif self.cell is not None and tag == "a" and attrs.get("href"):
            self.cell["links"].append(attrs["href"])

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.cell["text"] = _clean(" ".join(self.cell["text"])) or ""
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_tbody = False


def parse_current_advertisements(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)
    items = []
    for cells in parser.rows:
        if len(cells) < 10:
            continue
        planholder_link = next((link for link in cells[1]["links"] if "/planholders/" in link), None)
        if not planholder_link:
            continue
        ppid = planholder_link.rstrip("/").split("/")[-1]
        tracs = cells[4]["text"]
        items.append({
            "source_record_id": ppid,
            "route_county_milepost": cells[3]["text"],
            "tracs_number": tracs,
            "project_number": cells[5]["text"],
            "bid_opening_date": _date(cells[6]["text"]),
            "plans_status": cells[7]["text"],
            "addendum_count": int(cells[8]["text"] or 0) if str(cells[8]["text"]).isdigit() else None,
            "type_of_work": cells[9]["text"],
            "source_url": urljoin(BASE_URL, f"/projects/{ppid}"),
            "planholders_url": urljoin(BASE_URL, planholder_link),
        })
    return items


def parse_planholders(html: str) -> list[dict]:
    items = []
    tbodies = re.findall(r"<tbody>(.*?)</tbody>", html, re.I | re.S)
    if not tbodies:
        return items
    rows = [row for body in tbodies for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.I | re.S)]
    for row_html in rows:
        company = re.search(r"<address[^>]*class=[\"'][^\"']*vcard[^\"']*[\"'][^>]*>\s*<div>(.*?)</div>", row_html, re.I | re.S)
        if not company:
            continue
        def field(pattern):
            match = re.search(pattern, row_html, re.I | re.S)
            if not match:
                return None
            return _clean(unescape(re.sub(r"<[^>]+>", " ", match.group(1))))
        company_name = field(r"<address[^>]*class=[\"'][^\"']*vcard[^\"']*[\"'][^>]*>\s*<div>(.*?)</div>")
        address = field(r"class=[\"']street-address[\"'][^>]*>(.*?)</div>")
        city = field(r"class=[\"']locality[\"'][^>]*>(.*?)</span>")
        state = field(r"class=[\"']region[\"'][^>]*>(.*?)</span>")
        postal = field(r"class=[\"']postal-code[\"'][^>]*>(.*?)</span>")
        phone = field(r"<div[^>]*class=[\"']tel[\"'][^>]*>\s*Tel:\s*(.*?)</div>")
        fax = field(r"class=[\"']fax[\"'][^>]*>(.*?)</span>")
        dates = re.findall(r"<td[^>]*>\s*(\d{1,2}/\d{1,2}/\d{4})\s*</td>", row_html, re.I)
        company_name = _clean(company_name)
        if not company_name or company_name.casefold() in {"proposal pamphlet", "company"}:
            continue
        items.append({
            "company_name": company_name,
            "normalized_name": normalize_company_name(company_name),
            "phone": phone, "fax": fax, "address_line_1": _clean(address),
            "city": _clean((city or "").rstrip(",")), "state": _clean(state), "postal_code": _clean(postal),
            "registered_date": _date(dates[-1]) if dates else None,
        })
    deduped = {}
    for item in items:
        prior = deduped.get(item["normalized_name"])
        if prior is None or (not prior.get("phone") and item.get("phone")):
            deduped[item["normalized_name"]] = item
    return list(deduped.values())


def _company_name_index(conn: sqlite3.Connection) -> dict[str, set[int]]:
    """Build an exact canonical-name index once per refresh.

    Normalizing the stored display/legal/DBA values again also repairs matching
    against older databases whose ``normalized_name`` retained an LLC suffix.
    """
    index: dict[str, set[int]] = {}
    for row in conn.execute(
            "SELECT id,normalized_name,display_name,legal_name,dba_name FROM companies "
            "WHERE lifecycle_state='active'"):
        for value in (row["normalized_name"], row["display_name"], row["legal_name"], row["dba_name"]):
            key = normalize_company_name(value)
            if key:
                index.setdefault(key, set()).add(row["id"])
    for row in conn.execute(
            "SELECT a.company_id,a.alias_name,a.normalized_alias FROM company_aliases a "
            "JOIN companies c ON c.id=a.company_id WHERE c.lifecycle_state='active'"):
        for value in (row["normalized_alias"], row["alias_name"]):
            key = normalize_company_name(value)
            if key:
                index.setdefault(key, set()).add(row["company_id"])
    return index


def _exact_company(name_index: dict[str, set[int]], normalized_name: str) -> int | None:
    ids = name_index.get(normalized_name, set())
    return next(iter(ids)) if len(ids) == 1 else None


def _fetch(url: str, *, session=None) -> str:
    if session is None:
        import requests
        session = requests.Session()
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.text


def refresh(conn: sqlite3.Connection, *, fetch=_fetch, max_projects: int | None = None) -> dict:
    """Fetch and transactionally upsert current ADOT ads and planholders."""
    retrieved = _now()
    opportunities = parse_current_advertisements(fetch(CURRENT_URL))
    if max_projects is not None:
        opportunities = opportunities[:max(0, int(max_projects))]
    active_keys = []
    stats = {"opportunities": 0, "planholders": 0, "matched_companies": 0,
             "contact_updates": 0, "evidence_records": 0, "unmatched_planholders": 0,
             "planholder_fetch_errors": 0}
    name_index = _company_name_index(conn)
    matched_company_ids: set[int] = set()
    evidence_keys: set[tuple[int, str]] = set()
    pages = {}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(opportunities)))) as pool:
        future_urls = {pool.submit(fetch, item["planholders_url"]): item["planholders_url"]
                       for item in opportunities}
        for future in as_completed(future_urls):
            url = future_urls[future]
            try:
                pages[url] = future.result()
            except Exception:
                pages[url] = ""
                stats["planholder_fetch_errors"] += 1
    with conn:
        for item in opportunities:
            key = item["source_record_id"]
            active_keys.append(key)
            conn.execute(
                "INSERT INTO public_bid_opportunities (source_type,source_record_id,tracs_number,project_number,"
                "route_county_milepost,bid_opening_date,plans_status,addendum_count,type_of_work,source_url,"
                "planholders_url,retrieved_at,is_active,created_at,updated_at) VALUES ('adot',?,?,?,?,?,?,?,?,?,?,?,1,?,?) "
                "ON CONFLICT(source_type,source_record_id) DO UPDATE SET tracs_number=excluded.tracs_number,"
                "project_number=excluded.project_number,route_county_milepost=excluded.route_county_milepost,"
                "bid_opening_date=excluded.bid_opening_date,plans_status=excluded.plans_status,"
                "addendum_count=excluded.addendum_count,type_of_work=excluded.type_of_work,source_url=excluded.source_url,"
                "planholders_url=excluded.planholders_url,retrieved_at=excluded.retrieved_at,is_active=1,updated_at=excluded.updated_at",
                (key, item["tracs_number"], item["project_number"], item["route_county_milepost"],
                 item["bid_opening_date"], item["plans_status"], item["addendum_count"], item["type_of_work"],
                 item["source_url"], item["planholders_url"], retrieved, retrieved, retrieved))
            opp_id = conn.execute(
                "SELECT id FROM public_bid_opportunities WHERE source_type='adot' AND source_record_id=?", (key,)
            ).fetchone()["id"]
            stats["opportunities"] += 1
            for holder in parse_planholders(pages.get(item["planholders_url"], "")):
                company_id = _exact_company(name_index, holder["normalized_name"])
                if company_id:
                    matched_company_ids.add(company_id)
                    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
                    if holder["phone"] and not _clean(company["main_phone"]):
                        conn.execute("UPDATE companies SET main_phone=?,updated_at=? WHERE id=?",
                                     (holder["phone"], retrieved, company_id))
                        stats["contact_updates"] += 1
                    conn.execute(
                        "INSERT INTO company_external_evidence (company_id,source_type,source_agency,evidence_type,"
                        "source_record_id,title,status,summary,effective_date,source_url,retrieved_at,confidence,"
                        "match_method,details_json,created_at,updated_at) VALUES (?,'adot','Arizona Department of Transportation',"
                        "'plan_holder',?,'ADOT project planholder',?, ?,?,?,?,95,'legal_name',NULL,?,?) "
                        "ON CONFLICT(company_id,source_type,evidence_type,source_record_id) DO UPDATE SET status=excluded.status,"
                        "summary=excluded.summary,effective_date=excluded.effective_date,source_url=excluded.source_url,"
                        "retrieved_at=excluded.retrieved_at,updated_at=excluded.updated_at",
                        (company_id, key, item["plans_status"],
                         f"{item['tracs_number']} · {item['type_of_work']} · bid opens {item['bid_opening_date'] or 'TBD'}",
                         holder["registered_date"], item["planholders_url"], retrieved, retrieved, retrieved))
                    evidence_keys.add((company_id, key))
                else:
                    stats["unmatched_planholders"] += 1
                conn.execute(
                    "INSERT INTO public_bid_planholders (opportunity_id,company_id,company_name,normalized_name,phone,fax,"
                    "address_line_1,city,state,postal_code,registered_date,source_url,retrieved_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id,normalized_name) DO UPDATE SET "
                    "company_id=excluded.company_id,company_name=excluded.company_name,phone=excluded.phone,fax=excluded.fax,"
                    "address_line_1=excluded.address_line_1,city=excluded.city,state=excluded.state,postal_code=excluded.postal_code,"
                    "registered_date=excluded.registered_date,source_url=excluded.source_url,retrieved_at=excluded.retrieved_at,"
                    "updated_at=excluded.updated_at",
                    (opp_id, company_id, holder["company_name"], holder["normalized_name"], holder["phone"], holder["fax"],
                     holder["address_line_1"], holder["city"], holder["state"], holder["postal_code"],
                     holder["registered_date"], item["planholders_url"], retrieved, retrieved, retrieved))
                stats["planholders"] += 1
        if active_keys:
            placeholders = ",".join("?" for _ in active_keys)
            conn.execute(f"UPDATE public_bid_opportunities SET is_active=0,updated_at=? WHERE source_type='adot' "
                         f"AND source_record_id NOT IN ({placeholders})", (retrieved, *active_keys))
    stats["matched_companies"] = len(matched_company_ids)
    stats["evidence_records"] = len(evidence_keys)
    return stats
