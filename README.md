# CMS Fee App

A standalone Windows desktop application to **manage, view, filter, and export** CMS fee schedule data for both **DMEPOS** and the **Physician Fee Schedule (PFS)**.

- Runs as a single **Windows `.exe`** (no installer required)
- Can also be run from source using Python
- Supports two schedule types side-by-side in the same app:
  - **DMEPOS HCPCS Fee Schedule** — per-state, non-rural/rural amounts
  - **Physician Fee Schedule (National)** — national facility and non-facility payment amounts

---

## Quick Start (Windows)

1. Download `CMSFeeApp-Setup.zip` from **[GitHub Releases](https://github.com/cjlitson/CMS-fee-app/releases)**.
2. Extract the ZIP to any temporary location.
3. Double-click `Install.bat` to run the installer.
4. A desktop shortcut will be created — double-click it to launch.

---

## Features

### DMEPOS HCPCS Fee Schedule
- **Auto-download** CMS DMEPOS fee schedules for user-selected states (2024 through current year)
- Filter by state, year, HCPCS group, code, and description keyword
- Rural ZIP classifier — enter a 5-digit ZIP to display rural (R) or non-rural (NR) allowable
- **Quarterly replace** semantics — re-sync updates without duplicating records

### Physician Fee Schedule (PFS) — National Payment Amount
- **Auto-download** CMS PFS National Payment Amount file (2024 through current year)
- Displays both **Non-Facility** and **Facility** national payment amounts per HCPCS/CPT code
- Filter by year, HCPCS code, and description keyword
- No state selection needed — PFS National is a single national rate

### Shared Features
- **Export** results to **CSV**, **Excel (.xlsx)**, or **PDF**
- **SQLite database** — all data stored locally, no server needed
- **Import log** — track what data has been loaded and when
- **Developer Tools** — SQL Publisher for Databricks/ODBC publishing

---

## Usage

1. **Select Schedule Type** — Use the "Schedule Type" dropdown at the top to switch between DMEPOS and PFS.

### DMEPOS Workflow
1. First launch: select your tracked states and years.
2. Go to `Settings → Manage States` to configure which states to track.
3. Click **Sync from CMS** to download the latest DMEPOS fee schedules.
4. Filter by state, year, HCPCS code, or keyword.
5. Enter a ZIP code in the toolbar to automatically display rural (R) or non-rural (NR) amounts.
6. Click **Export…** to save results as CSV, Excel, or PDF.

### PFS (Physician Fee Schedule) Workflow
1. Select **"Physician Fee Schedule (National)"** from the Schedule Type dropdown.
   - State and ZIP controls will be hidden (PFS is national, not state-specific).
2. Click **Sync from CMS** to download the PFS National Payment Amount file.
3. Filter by year, HCPCS code, or description keyword.
4. Results grid shows: HCPCS Code, Description, Non-Facility ($), Facility ($), Year, Source.
5. Click **Export…** to save results as CSV, Excel, or PDF.

---

## Data Sources

| Schedule | Source |
|---|---|
| DMEPOS HCPCS Fee Schedule | https://www.cms.gov/medicare/payment/fee-schedules/dmepos |
| PFS National Payment Amount | https://www.cms.gov/medicare/payment/fee-schedules/physician/national-payment-amount-file |

---

## Requirements (for running from source)

- Python **3.11+**
- Windows 10/11 (for `.exe` build)
- Dependencies in `requirements.txt`

---

## Run from Source

```bash
git clone https://github.com/cjlitson/CMS-fee-app.git
cd CMS-fee-app
pip install -r requirements.txt
python main.py
```

---

## Build the Windows `.exe`

Double-click `build.bat` or run from command prompt:

```bat
build.bat
```

Output: `dist\CMSFeeApp.exe` and `dist\CMSFeeApp-Setup.zip`.

---

## Database Schema

The app uses a local SQLite database with two primary fee schedule tables:

### `hcpcs_fees` (DMEPOS)
| Column | Type | Description |
|---|---|---|
| `hcpcs_code` | TEXT | HCPCS/procedure code |
| `description` | TEXT | Item description |
| `state_abbr` | TEXT | State abbreviation |
| `year` | INTEGER | Fee schedule year |
| `allowable_nr` | REAL | Non-rural allowable amount |
| `allowable_r` | REAL | Rural allowable amount |
| `modifier` | TEXT | HCPCS modifier |
| `data_source` | TEXT | Import source tag |

### `pfs_fees` (Physician Fee Schedule)
| Column | Type | Description |
|---|---|---|
| `hcpcs_code` | TEXT | CPT/HCPCS procedure code |
| `description` | TEXT | Procedure description |
| `year` | INTEGER | Fee schedule year |
| `payment_non_facility` | REAL | National non-facility payment amount |
| `payment_facility` | REAL | National facility payment amount |
| `data_source` | TEXT | Import source tag |

---

## Download Strategy

### DMEPOS Discovery
Multi-layer self-correcting strategy:
1. **24-hour URL cache** — reuses previously discovered URLs
2. **CMS RSS feed** (`https://www.cms.gov/rss/30881`) — structured XML discovery
3. **HTML scraping** — scrapes the CMS DMEPOS page and sub-pages
4. **Pattern tracker** — adapts to CMS URL convention changes automatically
5. **Hardcoded URL templates** — last-resort fallback

### PFS Discovery
1. **24-hour URL cache** — reuses previously discovered URLs
2. **HTML scraping** — scrapes the CMS PFS National Payment Amount page and sub-pages
3. **Hardcoded URL templates** — last-resort fallback covering known CMS naming conventions

---

## Project Structure

```
CMS-fee-app/
├── main.py                          # App entry point
├── requirements.txt                 # Python dependencies
├── build.bat                        # Windows .exe build script
├── Install.bat                      # Per-user batch installer (no admin required)
├── ui/
│   ├── main_window.py               # Main window + sync workers (DMEPOS + PFS)
│   ├── state_selector_dialog.py     # State management dialog
│   ├── year_selector_dialog.py      # Year management dialog
│   ├── import_dialog.py             # CSV import wizard
│   ├── export_dialog.py             # Export options dialog
│   └── dev_tools_dialog.py          # Developer Tools / SQL Publisher
├── core/
│   ├── database.py                  # SQLite operations (hcpcs_fees + pfs_fees)
│   ├── importer.py                  # DMEPOS CSV parsers
│   ├── cms_downloader.py            # DMEPOS auto-download (scrape + cache + fallback)
│   ├── pfs_downloader.py            # PFS auto-download (scrape + cache + fallback)
│   ├── pfs_importer.py              # PFS National Payment Amount file parser
│   ├── exporter.py                  # CSV / Excel / PDF export
│   ├── self_updater.py              # In-app self-update
│   ├── shortcut.py                  # Desktop shortcut creation helper
│   └── version.py                   # App version + GitHub release update checker
├── models/
│   └── schema.sql                   # Database schema reference
├── tests/
│   ├── test_cms_downloader.py       # DMEPOS downloader tests
│   ├── test_importer.py             # DMEPOS importer tests
│   ├── test_pfs_importer.py         # PFS importer tests
│   ├── fixtures/
│   │   └── pfs_sample_2025.csv      # Sample PFS data for tests
│   └── test_main_window.py          # UI tests
└── data/
    └── hcpcs_fees.db                # Auto-created SQLite database (gitignored)
```
