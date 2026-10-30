import unittest
from datetime import datetime, timedelta

from vca.scheduler import ScheduleError, is_due, last_due, parse


class ScheduleTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse("every 15m").minutes, 15)
        self.assertEqual(parse("07:30").kind, "daily")
        weekly = parse("lun 08:00")
        self.assertEqual((weekly.kind, weekly.weekday, weekly.hour), ("weekly", 0, 8))
        self.assertEqual(parse("1 09:00").day, 1)
        with self.assertRaises(ScheduleError):
            parse("25:00")
        with self.assertRaises(ScheduleError):
            parse("cada día")

    def test_weekly_last_due(self):
        thursday = datetime(2026, 10, 29, 12, 0)
        self.assertEqual(last_due(parse("mon 08:00"), thursday), datetime(2026, 10, 26, 8, 0))
        monday_early = datetime(2026, 11, 2, 7, 0)
        self.assertEqual(last_due(parse("mon 08:00"), monday_early), datetime(2026, 10, 26, 8, 0))

    def test_monthly_clamps_day(self):
        self.assertEqual(last_due(parse("31 09:00"), datetime(2026, 11, 30, 10, 0)), datetime(2026, 11, 30, 9, 0))
        self.assertEqual(last_due(parse("1 09:00"), datetime(2026, 11, 1, 8, 0)), datetime(2026, 10, 1, 9, 0))

    def test_is_due(self):
        s = parse("07:30")
        now = datetime(2026, 10, 30, 7, 31)
        self.assertTrue(is_due(s, now, datetime(2026, 10, 29, 7, 30)))
        self.assertFalse(is_due(s, now, datetime(2026, 10, 30, 7, 30)))
        self.assertFalse(is_due(s, now, None))

    def test_interval(self):
        s = parse("every 2m")
        now = datetime(2026, 10, 30, 12, 0)
        self.assertTrue(is_due(s, now, None))
        self.assertFalse(is_due(s, now, now - timedelta(minutes=1)))
        self.assertTrue(is_due(s, now, now - timedelta(minutes=2)))


if __name__ == "__main__":
    unittest.main()
