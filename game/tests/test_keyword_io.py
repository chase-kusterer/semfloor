"""Tests for inline keyword editing and CSV/XLSX import/export."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from game import keyword_io, services
from game.models import Game, Keyword


def _game():
    return Game.objects.create(name="IO", code="IOTEST")


class KeywordIOParseTests(TestCase):
    def test_parse_native_csv(self):
        csv_text = (
            "Keyword,Asset class,Volume,Conversion rate,Order value,Reserve price,Notes\n"
            "espresso machines,Mid-cap,4000,0.05,120,1.50,hello\n"
            "beans,,2000,3,30,0.40,\n"  # conversion given as percent
        )
        rows = keyword_io.parse_keyword_upload("kw.csv", csv_text.encode())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["label"], "espresso machines")
        self.assertEqual(rows[0]["conversion_rate"], 0.05)
        self.assertEqual(rows[1]["conversion_rate"], 0.03)  # 3 -> 0.03
        self.assertEqual(rows[0]["order_value"], Decimal("120.00"))

    def test_parse_gkp_utf16_tab_with_metadata_rows(self):
        # Real Keyword Planner exports: UTF-16, tab separated, 2 metadata lines.
        gkp = (
            "Keyword Stats 2026-07-01\n"
            "\n"
            "Keyword\tCurrency\tAvg. monthly searches\tThree month change\tYoY change\t"
            "Competition\tCompetition (indexed value)\t"
            "Top of page bid (low range)\tTop of page bid (high range)\n"
            "running shoes\tUSD\t12,000\t+5%\t+20%\tHigh\t88\t0.80\t2.40\n"
            "trail shoes\tUSD\t1K – 10K\t0%\t-3%\tLow\t20\t0.30\t0.90\n"
        )
        rows = keyword_io.parse_keyword_upload("gkp.csv", gkp.encode("utf-16"))
        self.assertEqual(len(rows), 2)
        r0, r1 = rows
        self.assertEqual(r0["label"], "running shoes")
        self.assertEqual(r0["search_volume"], 12000)
        self.assertEqual(r0["conversion_rate"], 0.04)          # High competition
        self.assertEqual(r0["reserve_price"], Decimal("0.80"))  # low bid
        self.assertEqual(r0["order_value"], Decimal("60.00"))   # 2.40 / 0.04
        self.assertIn("3-mo +5%", r0["notes"])
        self.assertEqual(r1["search_volume"], 5500)             # midpoint of 1K–10K
        self.assertEqual(r1["conversion_rate"], 0.012)          # Low competition

    def test_parse_xlsx(self):
        import io as _io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Keyword", "Asset class", "Volume", "Conversion rate",
                   "Order value", "Reserve price", "Notes"])
        ws.append(["yoga mats", "Commodity churn", 9000, 0.02, 28, 0.5, ""])
        buf = _io.BytesIO()
        wb.save(buf)
        rows = keyword_io.parse_keyword_upload("kw.xlsx", buf.getvalue())
        self.assertEqual(rows[0]["label"], "yoga mats")
        self.assertEqual(rows[0]["search_volume"], 9000)

    def test_parse_rejects_garbage(self):
        with self.assertRaises(keyword_io.KeywordImportError):
            keyword_io.parse_keyword_upload("x.csv", b"just,some,cells\n1,2,3\n")
        with self.assertRaises(keyword_io.KeywordImportError):
            keyword_io.parse_keyword_upload("x.csv", b"")

    def test_import_append_update_replace_and_export_roundtrip(self):
        game = _game()
        services.load_starter_pack(game)
        n = game.keywords.count()
        rows = [{"label": "google analytics", "asset_class": "Edited", "search_volume": 999,
                 "conversion_rate": 0.05, "order_value": Decimal("70.00"),
                 "reserve_price": Decimal("1.00"), "notes": ""},
                {"label": "brand new", "asset_class": "", "search_volume": 100,
                 "conversion_rate": 0.02, "order_value": Decimal("40.00"),
                 "reserve_price": Decimal("0.30"), "notes": ""}]
        created, updated = keyword_io.import_keywords(game, rows)
        self.assertEqual((created, updated), (1, 1))
        self.assertEqual(game.keywords.count(), n + 1)
        self.assertEqual(game.keywords.get(label="google analytics").search_volume, 999)
        # Replace mode wipes and reloads.
        created, updated = keyword_io.import_keywords(game, rows, replace=True)
        self.assertEqual((created, updated), (2, 0))
        self.assertEqual(game.keywords.count(), 2)
        # Export -> parse -> identical fundamentals.
        text = keyword_io.export_keywords_csv(game)
        reparsed = keyword_io.parse_keyword_upload("kw.csv", text.encode())
        self.assertEqual({r["label"] for r in reparsed}, {"google analytics", "brand new"})
        self.assertEqual(reparsed[0]["conversion_rate"], 0.05)


class KeywordSetupEndpointTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.c = Client()
        self.c.force_login(User.objects.get(username="prof"))
        self.game = _game()
        services.load_starter_pack(self.game)

    def test_inline_edit(self):
        kw = self.game.keywords.get(label="dubai")
        self.c.post(f"/g/{self.game.code}/setup/keywords/edit/", {
            "keyword_id": kw.id, "label": "dubai hotels", "asset_class": "Mid-cap",
            "search_volume": "7000", "conversion_rate": "4",  # percent form
            "order_value": "90", "reserve_price": "1.10",
        })
        kw.refresh_from_db()
        self.assertEqual(kw.label, "dubai hotels")
        self.assertEqual(kw.search_volume, 7000)
        self.assertEqual(kw.conversion_rate, 0.04)
        self.assertEqual(kw.order_value, Decimal("90.00"))

    def test_edit_rejects_duplicate_label(self):
        kw = self.game.keywords.get(label="dubai")
        self.c.post(f"/g/{self.game.code}/setup/keywords/edit/", {
            "keyword_id": kw.id, "label": "ninja",
            "search_volume": kw.search_volume, "conversion_rate": kw.conversion_rate,
            "order_value": kw.order_value, "reserve_price": kw.reserve_price,
        })
        kw.refresh_from_db()
        self.assertEqual(kw.label, "dubai")  # unchanged

    def test_upload_and_download(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_text = ("Keyword,Asset class,Volume,Conversion rate,Order value,Reserve price,Notes\n"
                    "espresso machines,Mid-cap,4000,0.05,120,1.50,\n")
        f = SimpleUploadedFile("kw.csv", csv_text.encode(), content_type="text/csv")
        r = self.c.post(f"/g/{self.game.code}/setup/keywords/import/", {"file": f, "mode": "append"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.game.keywords.filter(label="espresso machines").exists())
        r = self.c.get(f"/g/{self.game.code}/setup/keywords/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("espresso machines", r.content.decode())
        self.assertIn("attachment", r["Content-Disposition"])

    def test_setup_page_shows_definitions_and_editable_rows(self):
        r = self.c.get(f"/g/{self.game.code}/setup/")
        self.assertContains(r, "What Do the Asset Classes Mean?")
        self.assertContains(r, "Branded blue chip")
        self.assertContains(r, 'name="conversion_rate"')
        self.assertContains(r, "Download CSV")
