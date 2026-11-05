"""Regression checks against an isolated database, never the household history."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

TEMP = tempfile.TemporaryDirectory(prefix="dishwasher-tests-")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dishwasher_under_test", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{TEMP.name}/fresh/data/test.db", "DISHWASHER_TIMEZONE": "Europe/Moscow"}):
    spec.loader.exec_module(server)


class DishwasherTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        server.engine.dispose()
        TEMP.cleanup()

    def setUp(self):
        with server.SessionLocal() as db:
            db.query(server.Submission).delete()
            db.query(server.Event).delete()
            db.commit()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()

    def add(self, user="Тима", action="disassembled", token=None):
        return self.client.post("/event", data={"user": user, "action": action, "request_id": token or str(uuid4())}, headers={"Accept": "application/json"})

    def count(self):
        with server.SessionLocal() as db:
            return db.query(server.Event).count()

    def seed(self, user, action, when):
        with server.SessionLocal() as db:
            db.add(server.Event(user=user, action=action, created_at=when))
            db.commit()

    def test_first_run_and_assets(self):
        self.assertTrue(Path(TEMP.name, "fresh/data/test.db").exists())
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Начнём с чистого листа", response.text)
        self.assertNotIn("fonts.googleapis", response.text)
        for path in ["/stats", "/static/style.css", "/static/app.js", "/static/icon.svg", "/static/manifest.webmanifest"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_validation_cannot_create_arbitrary_events(self):
        for user, action in [("Кто-то", "assembled"), ("Тима", "washed")]:
            self.assertEqual(self.add(user, action).status_code, 422)
        self.assertEqual(self.add(token="bad-id").status_code, 422)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.client.get("/stats?period=oops").status_code, 422)

    def test_form_fallback_and_legacy_posts(self):
        response = self.client.post("/event", data={"user": "Алиса", "action": "assembled"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.headers["location"])
        self.assertIn("Посуда внутри", page.text)
        self.assertIn("Отметка сохранена", page.text)
        self.assertIn("/event/undo", page.text)
        self.assertEqual(self.count(), 1)

    def test_retries_and_double_taps_are_idempotent(self):
        token = str(uuid4())
        first = self.add(token=token)
        second = self.add(token=token)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.add(user="Мама", token=token).status_code, 409)
        self.assertEqual(self.count(), 1)

    def test_concurrent_requests_with_same_token(self):
        token = str(uuid4())
        def send(_):
            with TestClient(server.app) as client:
                return client.post("/event", data={"user": "Тима", "action": "disassembled", "request_id": token}, headers={"Accept": "application/json"})
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, range(6)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.count(), 1)

    def test_undo_is_idempotent_and_retry_does_not_recreate_event(self):
        token = str(uuid4())
        self.add(token=token)
        for _ in range(2):
            response = self.client.post("/event/undo", data={"token": token}, headers={"Accept": "application/json"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.add(token=token).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_only_latest_recent_event_can_be_undone(self):
        first = str(uuid4())
        second = str(uuid4())
        self.add(token=first)
        self.add(user="Папа", action="assembled", token=second)
        self.assertEqual(self.client.post("/event/undo", data={"token": first}, headers={"Accept": "application/json"}).status_code, 409)
        with server.SessionLocal() as db:
            db.query(server.Event).update({server.Event.created_at: server.utcnow() - timedelta(minutes=3)})
            db.commit()
        self.assertEqual(self.client.post("/event/undo", data={"token": second}, headers={"Accept": "application/json"}).status_code, 409)
        self.assertEqual(self.count(), 2)

    def test_stats_include_old_history_for_all_time(self):
        self.seed("Тима", "disassembled", server.utcnow() - timedelta(days=500))
        self.seed("Тима", "assembled", server.utcnow())
        response = self.client.get("/stats?user=Тима&period=all")
        self.assertEqual(response.context["total"], 2)
        self.assertEqual(response.context["unloaded"], 1)
        self.assertEqual(response.context["loaded"], 1)
        self.assertEqual(response.context["family_total"], 2)
        self.assertEqual(sum(cell.get("count", 0) for cell in response.context["cells"]), 1)
        self.assertEqual(self.client.get("/stats?period=week").context["total"], 1)

    def test_period_boundaries_and_local_calendar(self):
        # Moscow midnight is the preceding UTC date at 21:00.
        start = server.period_start("week")
        self.seed("Тима", "assembled", start - timedelta(seconds=1))
        self.seed("Тима", "disassembled", start)
        response = self.client.get("/stats?period=week")
        self.assertEqual(response.context["total"], 1)
        first_cell = next(cell for cell in response.context["cells"] if not cell["padding"])
        self.assertEqual(first_cell["count"], 1)
        self.assertEqual(response.context["family_total"], 1)
        start_date = server.local_date(start)
        self.assertEqual(len(response.context["cells"]) - 7, start_date.weekday())

    def test_streak_is_kept_until_today_ends(self):
        today = datetime.now(server.LOCAL_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        for days_ago in [1, 2, 3]:
            when = (today - timedelta(days=days_ago)).astimezone(timezone.utc).replace(tzinfo=None)
            self.seed("Тима", "disassembled", when)
        self.assertEqual(self.client.get("/stats").context["streak"], 3)

    def test_status_and_receipts_do_not_change_legacy_events(self):
        self.seed("Мама", "assembled", server.utcnow() - timedelta(days=2))
        self.assertEqual(self.client.get("/api/status").json()["latest_id"], 1)
        response = self.client.get("/")
        self.assertIn("Мама", response.text)
        self.assertIn("Посуда внутри", response.text)
        self.assertEqual(self.count(), 1)

    def test_revision_changes_when_sqlite_reuses_an_undone_event_id(self):
        token = str(uuid4())
        self.add(token=token)
        before = self.client.get("/api/status").json()
        self.client.post("/event/undo", data={"token": token})
        self.add(user="Мама")
        after = self.client.get("/api/status").json()
        self.assertNotEqual(before["revision"], after["revision"])

    def test_late_retry_still_confirms_saved_event_without_undo(self):
        token = str(uuid4())
        self.add(token=token)
        with server.SessionLocal() as db:
            db.query(server.Event).update({server.Event.created_at: server.utcnow() - timedelta(minutes=3)})
            db.commit()
        response = self.add(token=token)
        page = self.client.get(response.json()["redirect"])
        self.assertIn("Отметка сохранена", page.text)
        self.assertNotIn('action="/event/undo"', page.text)
        self.assertEqual(self.count(), 1)

    def test_achievements_and_older_events_remain_accessible(self):
        for i in range(12):
            self.seed("Тима", "disassembled", server.utcnow() - timedelta(days=i * 40))
        stats = self.client.get("/stats?period=week")
        self.assertEqual(stats.context["total"], 1)
        self.assertIn("Первые 10 дел", stats.context["badges"])
        home = self.client.get("/")
        self.assertEqual(len(home.context["events"]), 12)
        self.assertIn("Ещё записи", home.text)


if __name__ == "__main__":
    unittest.main()
