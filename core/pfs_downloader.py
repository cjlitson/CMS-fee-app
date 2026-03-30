"""CMS Physician Fee Schedule (PFS) National Payment Amount file downloader.

Downloads the CMS PFS National Payment Amount ZIP file for a selected year,
extracts the CSV data, and imports it into the local database.

CMS PFS National Payment Amount File source:
  https://www.cms.gov/medicare/payment/fee-schedules/physician/national-payment-amount-file
"""

import io
import json
import re as _re
import zipfile
from datetime import date, datetime, timezone
from urllib.parse import urljoin

import requests

from core.database import get_preference, set_preference
from core.pfs_importer import import_pfs_national_file

# CMS PFS National Payment Amount File page
CMS_PFS_PAGE = (
    "https://www.cms.gov/medicare/payment/fee-schedules/physician"
    "/national-payment-amount-file"
)

_PFS_URL_CACHE_KEY_PREFIX = "pfs_url_cache_"
_PFS_URL_CACHE_TTL_HOURS = 24
_PFS_AVAILABLE_YEARS_CACHE_KEY = "pfs_available_years_cache"

# Hardcoded URL templates as last-resort fallback.
# {year} = 4-digit year, {year2d} = 2-digit year.
_PFS_URL_TEMPLATES = [
    # Most recent CMS naming convention for PFS national payment amount
    "https://www.cms.gov/files/zip/{year}-pfs-national-payment-amount-file.zip",
    "https://www.cms.gov/files/zip/pfs{year2d}natio.zip",
    "https://www.cms.gov/files/zip/{year}pfsnatl.zip",
    "https://www.cms.gov/files/zip/{year}-national-payment-amount.zip",
    "https://www.cms.gov/files/zip/pfs-national-{year}.zip",
    # Older patterns
    "https://www.cms.gov/files/zip/{year}PFS-NatlPayAmnt.zip",
    "https://www.cms.gov/files/zip/{year}NatlPayAmnt.zip",
]

# Pattern that matches PFS national payment amount ZIP filenames on CMS pages
_PFS_ZIP_RE = _re.compile(
    r"(?i)(?:pfs|national.?payment|natlpay|natl.?pay|physician.?fee).*?\.zip$"
)

SUPPORTED_YEARS = list(range(2024, date.today().year + 1))


class PFSDownloadError(Exception):
    """Raised when the PFS download cannot be completed."""


# ---------------------------------------------------------------------------
# HTML link extractor (reused pattern from cms_downloader.py)
# ---------------------------------------------------------------------------

import html.parser


class _LinkExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr, val in attrs:
                if attr == "href" and val:
                    self.links.append(val)


# ---------------------------------------------------------------------------
# URL discovery / cache helpers
# ---------------------------------------------------------------------------

def _get_cached_pfs_urls(year):
    """Return cached PFS URL list for *year* if still valid."""
    key = f"{_PFS_URL_CACHE_KEY_PREFIX}{year}"
    raw = get_preference(key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        cached_at = datetime.fromisoformat(data["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours > _PFS_URL_CACHE_TTL_HOURS:
            return []
        return data["urls"]
    except Exception:
        return []


def _set_cached_pfs_urls(year, urls):
    """Cache PFS URL list for *year*."""
    key = f"{_PFS_URL_CACHE_KEY_PREFIX}{year}"
    data = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "urls": urls,
    }
    set_preference(key, json.dumps(data))


def _scrape_pfs_urls(year):
    """Scrape the CMS PFS page for national payment amount ZIP links for *year*.

    Returns a deduplicated list of absolute ZIP URLs, or an empty list on any error.
    """
    year_str = str(year)
    year2d = year_str[-2:]

    def _collect_zip_urls(html_text, base_url, seen, urls):
        extractor = _LinkExtractor()
        extractor.feed(html_text)
        for href in extractor.links:
            abs_url = urljoin(base_url, href)
            filename = abs_url.split("/")[-1].lower()
            # Match PFS national payment amount file patterns
            if not filename.endswith(".zip"):
                continue
            # Look for year match and PFS-related naming
            if year_str not in abs_url and year2d not in filename:
                continue
            # Reject clearly non-PFS files
            lower = abs_url.lower()
            if any(x in lower for x in ["dme", "dmepos", "rural", "locality"]):
                continue
            if _PFS_ZIP_RE.search(filename) or year_str in filename or year2d in filename:
                if abs_url not in seen:
                    seen.add(abs_url)
                    urls.append(abs_url)

    try:
        resp = requests.get(CMS_PFS_PAGE, timeout=15)
        if resp.status_code != 200:
            return []

        seen = set()
        urls = []
        _collect_zip_urls(resp.text, CMS_PFS_PAGE, seen, urls)

        # Follow sub-page links that may have year-specific detail pages
        extractor = _LinkExtractor()
        extractor.feed(resp.text)
        for href in extractor.links:
            abs_url = urljoin(CMS_PFS_PAGE, href)
            lower = abs_url.lower()
            if year_str in lower or year2d in lower:
                if "physician" in lower or "pfs" in lower or "payment" in lower or "fee-schedule" in lower:
                    try:
                        sub_resp = requests.get(abs_url, timeout=15)
                        if sub_resp.status_code == 200:
                            _collect_zip_urls(sub_resp.text, abs_url, seen, urls)
                    except Exception:
                        pass

        return urls
    except Exception:
        return []


def _generate_template_candidates(year):
    """Generate candidate URLs from hardcoded templates for *year*."""
    year2d = str(year)[-2:]
    candidates = []
    for tmpl in _PFS_URL_TEMPLATES:
        url = tmpl.format(year=year, year2d=year2d)
        candidates.append(url)
    return candidates


def discover_available_pfs_years():
    """Scrape the CMS PFS page; return a set of available years.

    Results are cached for 24 hours.  Returns SUPPORTED_YEARS on any error
    so the UI always shows options.
    """
    raw = get_preference(_PFS_AVAILABLE_YEARS_CACHE_KEY)
    if raw:
        try:
            data = json.loads(raw)
            cached_at = datetime.fromisoformat(data["cached_at"])
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
            if age_hours <= _PFS_URL_CACHE_TTL_HOURS:
                return set(data["years"])
        except Exception:
            pass

    years = set()
    try:
        resp = requests.get(CMS_PFS_PAGE, timeout=15)
        if resp.status_code == 200:
            extractor = _LinkExtractor()
            extractor.feed(resp.text)
            for href in extractor.links:
                # Look for 4-digit years in links
                m = _re.search(r"\b(20\d{2})\b", href)
                if m:
                    y = int(m.group(1))
                    if 2020 <= y <= date.today().year + 1:
                        years.add(y)
                # Also check 2-digit year patterns like /pfs26 or pfs26.zip
                m = _re.search(r"(?:pfs|physician|national.?payment)(\d{2})(?:[^/\d]|$)", href.lower())
                if m:
                    y = 2000 + int(m.group(1))
                    if 2020 <= y <= date.today().year + 1:
                        years.add(y)
    except Exception:
        pass

    if not years:
        return set(SUPPORTED_YEARS)

    # Cache result
    set_preference(_PFS_AVAILABLE_YEARS_CACHE_KEY, json.dumps({
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "years": sorted(years),
    }))
    return years


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_pfs_file_from_zip(zip_bytes):
    """Extract the most likely PFS data file from a ZIP archive.

    Returns (filename, file_bytes) for the best candidate file,
    or raises PFSDownloadError if no suitable file is found.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        # Priority order of candidate files:
        # 1. CSV/TXT files containing "national", "payment", "pfs", "natl"
        # 2. Any CSV file
        # 3. Any TXT file
        priority1 = []
        priority2 = []
        priority3 = []
        for name in names:
            lower = name.lower()
            if lower.endswith(".csv") or lower.endswith(".txt"):
                if any(kw in lower for kw in ["national", "payment", "pfs", "natl", "natlpay"]):
                    priority1.append(name)
                elif lower.endswith(".csv"):
                    priority2.append(name)
                elif lower.endswith(".txt"):
                    priority3.append(name)

        candidates = priority1 or priority2 or priority3
        if not candidates:
            raise PFSDownloadError(
                f"No CSV/TXT data file found in ZIP. Contents: {names}"
            )

        # Pick the largest candidate (usually the main data file)
        best = max(candidates, key=lambda n: zf.getinfo(n).file_size)
        return best, zf.read(best)


# ---------------------------------------------------------------------------
# Main download orchestrator
# ---------------------------------------------------------------------------

def download_pfs_fees(year, progress_callback=None):
    """Download, extract, and import the PFS National Payment Amount file for *year*.

    Discovery strategy (in order):
    1. URL cache (24-hour TTL)
    2. HTML scraping of CMS PFS page + sub-pages
    3. Hardcoded URL templates

    Args:
        year: The fee schedule year to download.
        progress_callback: Optional callable(str) for progress messages.

    Returns number of records imported.
    Raises PFSDownloadError on failure.
    """
    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    year_str = str(year)
    data_source = f"pfs_{year_str}"
    _progress(f"Discovering PFS {year} download URL…")

    # Build candidate URL list
    candidates = []

    # 1. Check cache
    cached = _get_cached_pfs_urls(year)
    if cached:
        _progress(f"Using cached URL for PFS {year}…")
        candidates.extend(cached)

    # 2. Scrape CMS page
    if not candidates:
        _progress(f"Scraping CMS PFS page for {year}…")
        scraped = _scrape_pfs_urls(year)
        if scraped:
            _set_cached_pfs_urls(year, scraped)
            candidates.extend(scraped)

    # 3. Template fallback
    candidates.extend(_generate_template_candidates(year))

    # Deduplicate while preserving order
    seen = set()
    unique_candidates = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append(url)

    last_error = None
    for url in unique_candidates:
        _progress(f"Trying: {url}")
        try:
            # HEAD check first to avoid downloading 404 pages
            head = requests.head(url, timeout=10, allow_redirects=True)
            if head.status_code not in (200, 302, 301):
                continue

            _progress(f"Downloading PFS {year} from {url}…")
            resp = requests.get(url, timeout=120, stream=True)
            if resp.status_code != 200:
                continue

            zip_bytes = resp.content
            if len(zip_bytes) < 1000:
                continue

            # Verify it's a valid ZIP
            if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
                _progress(f"Downloaded file is not a valid ZIP, skipping {url}")
                continue

            _progress(f"Extracting PFS data file from ZIP…")
            filename, file_bytes = _extract_pfs_file_from_zip(zip_bytes)
            _progress(f"Extracted: {filename}")

            # Write to temp file for parsing
            import tempfile
            import os
            suffix = ".csv" if filename.lower().endswith(".csv") else ".txt"
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=suffix, delete=False
            ) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            try:
                _progress(f"Importing PFS {year} data…")
                count = import_pfs_national_file(
                    tmp_path,
                    year=year,
                    data_source=data_source,
                    file_name=filename,
                )
            finally:
                os.unlink(tmp_path)

            # Cache the successful URL
            _set_cached_pfs_urls(year, [url])
            _progress(f"PFS {year}: imported {count:,} records.")
            return count

        except PFSDownloadError:
            raise
        except Exception as e:
            last_error = str(e)
            _progress(f"Failed ({url}): {e}")
            continue

    raise PFSDownloadError(
        f"Could not download PFS national payment amount file for {year}. "
        f"Last error: {last_error or 'No candidates succeeded'}"
    )
