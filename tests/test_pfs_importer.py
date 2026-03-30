"""Tests for the PFS National Payment Amount file importer."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the project root is on sys.path so ``core`` can be imported directly.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.database as db_module
from core.pfs_importer import parse_pfs_national_file, import_pfs_national_file


# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PFS_SAMPLE_CSV = FIXTURE_DIR / "pfs_sample_2025.csv"


# ---------------------------------------------------------------------------
# Helper to redirect DB to a temporary file for isolation
# ---------------------------------------------------------------------------

class _TempDbMixin:
    """Mixin that redirects core.database to a fresh temp DB for each test."""

    def setUp(self):
        super().setUp()
        self._db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        self._orig_db_path = db_module.DB_PATH
        db_module.DB_PATH = Path(db_path)
        db_module.init_db()

    def tearDown(self):
        super().tearDown()
        db_module.DB_PATH = self._orig_db_path
        try:
            os.unlink(str(db_module.DB_PATH))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Parser tests (no DB required)
# ---------------------------------------------------------------------------

class TestParsePfsNationalFile(unittest.TestCase):
    """Tests for ``parse_pfs_national_file``."""

    def test_parse_fixture_csv(self):
        """Parse the bundled sample CSV fixture and check record count + fields."""
        records = parse_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025)
        self.assertGreater(len(records), 0, "Expected at least one record")
        # Check required fields present in every record
        for r in records:
            self.assertIn("hcpcs_code", r)
            self.assertIn("year", r)
            self.assertIn("payment_non_facility", r)
            self.assertIn("payment_facility", r)
            self.assertEqual(r["year"], 2025)
            self.assertIsNotNone(r["hcpcs_code"])
            self.assertNotEqual(r["hcpcs_code"].strip(), "")

    def test_parse_fixture_csv_record_count(self):
        """Fixture has 10 data rows → expect 10 records."""
        records = parse_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025)
        self.assertEqual(len(records), 10)

    def test_parse_fixture_csv_amounts(self):
        """Check that known codes have the expected payment amounts."""
        records = parse_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025)
        by_code = {r["hcpcs_code"]: r for r in records}

        self.assertIn("99213", by_code)
        self.assertAlmostEqual(by_code["99213"]["payment_non_facility"], 85.50, places=2)
        self.assertAlmostEqual(by_code["99213"]["payment_facility"], 62.00, places=2)

        self.assertIn("71046", by_code)
        self.assertAlmostEqual(by_code["71046"]["payment_non_facility"], 40.00, places=2)
        self.assertAlmostEqual(by_code["71046"]["payment_facility"], 40.00, places=2)

    def test_parse_csv_default_year(self):
        """When year is not provided, records still get a year (current year)."""
        from datetime import datetime
        records = parse_pfs_national_file(str(PFS_SAMPLE_CSV))
        self.assertTrue(all(r["year"] == datetime.now().year for r in records))

    def test_parse_pipe_delimited(self):
        """Parser handles pipe-delimited files."""
        content = (
            "HCPCS|Description|Non-Facility Amount|Facility Amount\n"
            "99201|Office visit low|76.00|55.00\n"
            "99202|Office visit mod|110.00|77.00\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertEqual(len(records), 2)
            self.assertAlmostEqual(records[0]["payment_non_facility"], 76.00, places=2)
            self.assertAlmostEqual(records[0]["payment_facility"], 55.00, places=2)
        finally:
            os.unlink(tmp)

    def test_parse_tab_delimited(self):
        """Parser handles tab-delimited files."""
        content = (
            "HCPCS_CD\tLong_description\tNon_fac_total\tFac_total\n"
            "99213\tOffice visit est\t85.50\t62.00\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertEqual(len(records), 1)
            self.assertAlmostEqual(records[0]["payment_non_facility"], 85.50, places=2)
        finally:
            os.unlink(tmp)

    def test_parse_with_preamble(self):
        """Parser skips preamble rows before the header."""
        content = (
            "CMS Physician Fee Schedule - National Payment Amount File\n"
            "Generated: 2025-01-01\n"
            "\n"
            "HCPCS_CD,Long_description,Non_fac_total,Fac_total\n"
            "99213,Office visit est,85.50,62.00\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["hcpcs_code"], "99213")
        finally:
            os.unlink(tmp)

    def test_parse_skips_empty_rows(self):
        """Parser skips rows with no payment amounts."""
        content = (
            "HCPCS_CD,Long_description,Non_fac_total,Fac_total\n"
            "99213,Office visit,85.50,62.00\n"
            ",,\n"  # completely empty
            "99214,Another visit,125.00,89.00\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertEqual(len(records), 2)
        finally:
            os.unlink(tmp)

    def test_parse_normalizes_hcpcs_to_uppercase(self):
        """HCPCS codes containing letters are normalized to uppercase."""
        content = (
            "HCPCS_CD,Long_description,Non_fac_total,Fac_total\n"
            "a0425,Ground mileage,10.00,10.00\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertEqual(records[0]["hcpcs_code"], "A0425")
        finally:
            os.unlink(tmp)

    def test_parse_amount_handles_dollar_signs(self):
        """Parser strips dollar signs from amounts."""
        content = (
            "HCPCS_CD,Long_description,Non_fac_total,Fac_total\n"
            '99213,Office visit,"$85.50","$62.00"\n'
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            records = parse_pfs_national_file(tmp, year=2025)
            self.assertAlmostEqual(records[0]["payment_non_facility"], 85.50, places=2)
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Import helper tests (with DB)
# ---------------------------------------------------------------------------

class TestImportPfsNationalFile(_TempDbMixin, unittest.TestCase):
    """Tests for ``import_pfs_national_file``."""

    def test_import_fixture_csv(self):
        """Import fixture CSV and verify records end up in the database."""
        count = import_pfs_national_file(
            str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test"
        )
        self.assertEqual(count, 10)

        from core.database import get_pfs_fees
        records = get_pfs_fees(year=2025)
        self.assertEqual(len(records), 10)

    def test_import_replace_semantics(self):
        """Re-importing the same year deletes old rows before inserting new ones."""
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")

        from core.database import get_pfs_fees
        records = get_pfs_fees(year=2025)
        # Should still be 10, not 20
        self.assertEqual(len(records), 10)

    def test_import_empty_file_raises(self):
        """Importing an empty file raises ValueError and preserves existing data."""
        # First import: should succeed
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")

        # Create empty/header-only file
        content = "HCPCS_CD,Long_description,Non_fac_total,Fac_total\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(content)
            tmp = f.name

        try:
            with self.assertRaises(ValueError):
                import_pfs_national_file(tmp, year=2025, data_source="pfs_test")
        finally:
            os.unlink(tmp)

        # Existing data should still be present
        from core.database import get_pfs_fees
        records = get_pfs_fees(year=2025)
        self.assertEqual(len(records), 10, "Existing data should not be wiped on empty import")

    def test_import_adds_log_entry(self):
        """import_pfs_national_file creates an entry in the import_log table."""
        import_pfs_national_file(
            str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test",
            file_name="pfs_sample_2025.csv"
        )
        from core.database import get_import_log
        log = get_import_log()
        self.assertGreater(len(log), 0)
        self.assertEqual(log[0]["file_name"], "pfs_sample_2025.csv")
        self.assertEqual(log[0]["states"], "National")

    def test_get_available_pfs_years(self):
        """After importing, get_available_pfs_years returns the imported year."""
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")

        from core.database import get_available_pfs_years
        years = get_available_pfs_years()
        self.assertIn(2025, years)

    def test_get_pfs_fees_filter_by_code(self):
        """get_pfs_fees filters correctly by hcpcs_code."""
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")

        from core.database import get_pfs_fees
        records = get_pfs_fees(hcpcs_code="99213")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["hcpcs_code"], "99213")

    def test_get_pfs_fees_filter_by_keyword(self):
        """get_pfs_fees filters correctly by description keyword."""
        import_pfs_national_file(str(PFS_SAMPLE_CSV), year=2025, data_source="pfs_test")

        from core.database import get_pfs_fees
        records = get_pfs_fees(keyword="electrocardiogram")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["hcpcs_code"], "93000")


if __name__ == "__main__":
    unittest.main()
