"""CMS Physician Fee Schedule (PFS) National Payment Amount file parser.

Parses the CMS PFS National Payment Amount file and normalizes records
into dicts suitable for ``insert_pfs_fees()``.

Data source:
  https://www.cms.gov/medicare/payment/fee-schedules/physician/national-payment-amount-file
"""

import csv
import re as _re
from datetime import datetime

from core.database import insert_pfs_fees, add_import_log, delete_pfs_fees_by_year_source


# ---------------------------------------------------------------------------
# Amount parser (reused from importer.py pattern)
# ---------------------------------------------------------------------------

def _parse_amount(raw):
    """Parse a numeric amount string, returning float or None."""
    try:
        cleaned = str(raw).replace("$", "").replace(",", "").strip()
        val = float(cleaned)
        return val if val != 0.0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Header detection helpers
# ---------------------------------------------------------------------------

def _normalize_col(col):
    """Normalize a column header string for matching."""
    return col.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")


def _find_header_line(fp):
    """Scan *fp* and return (line_index, line_text) for the first header-looking line.

    A header line must contain at least 3 of the common PFS column keywords.
    Returns (0, first_line) as fallback.
    """
    keywords = {"hcpcs", "cpt", "code", "description", "facility", "non", "payment", "modifier", "work", "rvu"}
    fp.seek(0)
    for idx, line in enumerate(fp):
        lower = line.lower()
        hits = sum(1 for kw in keywords if kw in lower)
        if hits >= 2:
            return idx, line
    return 0, None


# ---------------------------------------------------------------------------
# Column map detection
# ---------------------------------------------------------------------------

def _detect_pfs_columns(header_row):
    """Return a dict mapping logical names to column indices from *header_row* list.

    Logical names: hcpcs_code, description, payment_non_facility, payment_facility.

    The PFS National Payment Amount file has changed column layouts over the years.
    This function handles the known layouts robustly by normalizing column names.
    """
    norm = [_normalize_col(h) for h in header_row]

    def _find(candidates, norm_list):
        for c in candidates:
            for i, n in enumerate(norm_list):
                if c in n:
                    return i
        return None

    hcpcs_idx = _find(["hcpcs_cd", "hcpcs_code", "hcpcs", "cpt_hcpcs", "procedure_code", "proc_cd", "cpt"], norm)
    desc_idx = _find(["long_description", "description", "short_desc", "item_description"], norm)

    # Non-facility: look for columns with "non_fac" or "nonfac" or "non-facility" in name
    nf_idx = _find([
        "non_fac_pe_rvu", "non_fac_total", "nonfacility_pe_rvu", "nonfacility_total",
        "non_facility_total", "non_fac_limiting", "nonfac_limiting",
        "non_fac_pricing_indicator", "nonfac",
        # also try generic "non_facility" or "non-fac"
        "non_fac", "nonfacility",
    ], norm)
    # Also specifically look for "non-facility national payment amount" columns
    for i, n in enumerate(norm):
        if "non" in n and ("facility" in n or "fac" in n) and ("amount" in n or "payment" in n or "total" in n or "limiting" in n):
            nf_idx = i
            break

    # Facility: look for "facility" columns (but not "non-facility")
    f_idx = None
    for i, n in enumerate(norm):
        if "facility" in n or "fac" in n:
            if "non" not in n and "nonfac" not in n and i != nf_idx:
                if "amount" in n or "payment" in n or "total" in n or "limiting" in n or "pe_rvu" in n:
                    f_idx = i
                    break

    # Fallback: try common PFS column names
    if nf_idx is None:
        nf_idx = _find(["non_fac_payment", "nf_payment", "national_payment_nonfac"], norm)
    if f_idx is None:
        f_idx = _find(["fac_payment", "f_payment", "national_payment_fac", "facility_payment"], norm)

    # Last resort: look for columns 17 and 22 which are the typical positions
    # in the CMS national payment amount file (non-fac = col 17, fac = col 22)
    # But only if we found the hcpcs column to confirm we're reading the right file
    if hcpcs_idx is not None and nf_idx is None and len(norm) > 17:
        nf_idx = 17
    if hcpcs_idx is not None and f_idx is None and len(norm) > 22:
        f_idx = 22

    return {
        "hcpcs_code": hcpcs_idx,
        "description": desc_idx,
        "payment_non_facility": nf_idx,
        "payment_facility": f_idx,
    }


# ---------------------------------------------------------------------------
# Main PFS file parser
# ---------------------------------------------------------------------------

def parse_pfs_national_file(path, year=None, data_source="pfs_download"):
    """Parse a CMS PFS National Payment Amount file and return normalized record dicts.

    The file may be CSV or pipe/tab-delimited.  Handles preamble rows before
    the real header.

    Args:
        path: Path to the extracted CSV/TXT file.
        year: Year to tag all records with.  If None, uses current year.
        data_source: Data source tag for import_log and DB storage.

    Returns list of dicts with keys:
        year, hcpcs_code, description, payment_non_facility, payment_facility,
        data_source.

    Raises ValueError if zero records are parsed (to guard against wipe with empty data).
    """
    if year is None:
        year = datetime.now().year

    # Detect delimiter
    delimiter = ","
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 30:
                    break
                line = line.strip()
                if not line:
                    continue
                tabs = line.count("\t")
                pipes = line.count("|")
                commas = line.count(",")
                tildes = line.count("~")
                max_count = max(tabs, pipes, commas, tildes)
                if max_count < 3:
                    continue
                if tabs == max_count:
                    delimiter = "\t"
                elif pipes == max_count:
                    delimiter = "|"
                elif commas == max_count:
                    delimiter = ","
                elif tildes == max_count:
                    delimiter = "~"
                break
    except Exception:
        pass

    records = []
    col_map = None

    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fp:
        # Find header line
        header_idx, header_line = _find_header_line(fp)
        fp.seek(0)
        for _ in range(header_idx):
            next(fp)

        reader = csv.reader(fp, delimiter=delimiter)
        header_row = None

        for row in reader:
            if not any(c.strip() for c in row):
                continue

            # First non-empty row at/after header_idx is the header
            if header_row is None:
                header_row = row
                col_map = _detect_pfs_columns(header_row)
                continue

            if col_map is None:
                continue

            hcpcs_idx = col_map.get("hcpcs_code")
            desc_idx = col_map.get("description")
            nf_idx = col_map.get("payment_non_facility")
            f_idx = col_map.get("payment_facility")

            def _get(idx):
                if idx is None or idx >= len(row):
                    return ""
                return row[idx].strip()

            hcpcs = _get(hcpcs_idx).upper().strip()
            if not hcpcs:
                continue
            # Skip rows that look like headers or totals
            if hcpcs in ("HCPCS", "CPT", "HCPCS_CD", "CODE"):
                continue

            description = _get(desc_idx)
            payment_nf = _parse_amount(_get(nf_idx))
            payment_f = _parse_amount(_get(f_idx))

            # Skip rows with no payment amounts at all
            if payment_nf is None and payment_f is None:
                continue

            records.append({
                "year": year,
                "hcpcs_code": hcpcs,
                "description": description,
                "payment_non_facility": payment_nf,
                "payment_facility": payment_f,
                "data_source": data_source,
            })

    return records


# ---------------------------------------------------------------------------
# High-level import helper
# ---------------------------------------------------------------------------

def import_pfs_national_file(path, year, data_source="pfs_download", file_name=None):
    """Parse and import a PFS National Payment Amount file into the database.

    Uses replace semantics: deletes existing rows for (year, data_source)
    before inserting new ones.  If parse yields 0 rows, raises ValueError
    so existing data is not wiped.

    Args:
        path: Path to the extracted CSV/TXT file.
        year: Year for the data.
        data_source: Data source tag.
        file_name: Filename for the import log entry.

    Returns number of records imported.
    Raises ValueError if no records parsed.
    """
    records = parse_pfs_national_file(path, year=year, data_source=data_source)
    if not records:
        raise ValueError(
            f"PFS parser returned 0 records from '{path}'. "
            "Import aborted to preserve existing data."
        )

    delete_pfs_fees_by_year_source(year, data_source)
    insert_pfs_fees(records, data_source=data_source)

    add_import_log(
        file_name=file_name or str(path),
        source=data_source,
        record_count=len(records),
        states="National",
    )
    return len(records)
