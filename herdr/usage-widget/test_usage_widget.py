import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import usage_widget as widget


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


class UsageWidgetTests(unittest.TestCase):
    def test_codex_weekly_only_is_named_by_duration(self):
        weekly = {"usedPercent": 60, "windowDurationMins": 10080, "resetsAt": 1795598601}
        windows = widget.codex_windows({
            "rateLimits": {"primary": weekly, "secondary": None},
            "rateLimitsByLimitId": {"codex": {"primary": weekly}},
        })
        self.assertEqual([(row.label, row.used) for row in windows], [("7d", 60)])

    def test_claude_fable_has_its_own_weekly_gauge(self):
        reset = "2026-11-25T00:59:59.701567+00:00"
        windows = widget.claude_windows({
            "rate_limits_available": True,
            "rate_limits": {
                "five_hour": {"utilization": 42, "resets_at": reset},
                "seven_day": {"utilization": 65, "resets_at": reset},
                "model_scoped": [{"display_name": "Fable", "utilization": 32, "resets_at": reset}],
                "internal": {"utilization": 99, "resets_at": reset},
            },
        })
        self.assertEqual(
            [(row.label, row.used) for row in windows],
            [("5h", 42), ("7d", 65), ("Fable", 32)],
        )
        self.assertEqual(windows[-1].resets_at, timestamp(reset))

    def test_bar_shows_percent_used_and_local_reset_time_across_dst(self):
        now = timestamp("2026-10-24T14:00:00+00:00")
        reset = timestamp("2026-10-25T15:00:00+00:00")
        try:
            with patch.dict(os.environ, {"TZ": "Europe/Stockholm"}):
                time.tzset()
                reading = widget.Reading((widget.Window("7d", 90, reset),), now)
                line = widget.bar_line({"codex": reading}, now)
        finally:
            time.tzset()
        self.assertIn("Used", line)
        self.assertIn("7d 90% @ Sun 16:00", line)
        self.assertIn("CC unavailable", line)

    def test_each_herdr_status_entry_keeps_its_full_reset_times(self):
        now = timestamp("2026-11-25T00:00:00+00:00")
        codex = widget.Reading((widget.Window("5h", 44, now + 3600), widget.Window("7d", 65, now + 86400)), now)
        claude = widget.Reading((*codex.windows, widget.Window("Fable", 32, now + 86400)), now)
        readings = {"codex": codex, "claude": claude}
        for provider, last_label in (("codex", "7d"), ("claude", "Fable")):
            with self.subTest(provider=provider):
                line = widget.bar_line(readings, now, provider)
                self.assertLessEqual(len(line), 80)
                self.assertRegex(line, rf"{last_label} \d+% @ \w+ \d{{2}}:\d{{2}}$")

    def test_narrow_layout_drops_usage_to_keep_the_shortcut_strip(self):
        now = timestamp("2026-11-25T00:00:00+00:00")
        weekly = widget.Window("7d", 50, now + 86400)
        fable = widget.Window("Fable", 65, now + 86400)
        readings = {"codex": widget.Reading((weekly,), now), "claude": widget.Reading((weekly, fable), now)}
        self.assertEqual(widget.status_entry(readings, now, "claude", 160), "")
        self.assertIn("Fable 65%", widget.status_entry(readings, now, "claude", 219))

    def test_local_reset_uses_nearest_minute(self):
        before = timestamp("2026-11-25T00:59:59.701567+00:00")
        after = timestamp("2026-11-25T01:00:00.301567+00:00")
        self.assertEqual(widget.local_reset(before), widget.local_reset(after))

    def test_malformed_cache_does_not_prevent_other_provider_rendering(self):
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root) / "usage.json"
            cache.write_text(
                '{"codex":{"windows":[{"label":"7d","used":null,"resets_at":null}],"checked_at":1795500000,"stale":false},'
                '"claude":{"windows":[{"label":"5h","used":12,"resets_at":null}],"checked_at":1795500000,"stale":false}}'
            )
            line = widget.bar_line(widget.read_cache(cache), 1795500000)
            self.assertIn("CDX unavailable", line)
            self.assertIn("CC 5h 12%", line)

    def test_failed_refresh_keeps_other_provider_and_marks_old_data_stale(self):
        now = timestamp("2026-11-25T00:00:00+00:00")
        codex = widget.Reading((widget.Window("7d", 20, None),), now - 60)
        claude = widget.Window("Fable", 30, None)
        readings = widget.update_readings({"codex": codex}, [None, (claude,)], now)
        line = widget.bar_line(readings, now)
        self.assertIn("CDX 7d 20% (stale)", line)
        self.assertIn("CC Fable 30%", line)

    def test_expired_window_does_not_show_previous_usage_as_current(self):
        now = timestamp("2026-11-25T00:00:00+00:00")
        reading = widget.Reading((widget.Window("5h", 100, now - 1),), now - 60)
        line = widget.bar_line({"claude": reading}, now)
        self.assertIn("5h reset due", line)
        self.assertNotIn("100%", line)

    def test_a_slow_probe_does_not_skip_the_next_five_minute_refresh(self):
        now = int(time.time()) - 10
        result = [(widget.Window("7d", 44, None),), None]
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root) / "usage.json"
            with patch.object(widget, "fetch", return_value=result) as fetch:
                with patch.object(widget.time, "time", side_effect=[now, now + 10, now + 300, now + 310]):
                    widget.refresh_cache(cache)
                    next_reading = widget.refresh_cache(cache)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(next_reading["codex"].checked_at, now + 310)

    def test_private_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root) / "usage.json"
            readings = {"codex": widget.Reading((widget.Window("7d", 0, None),), 1795500000)}
            widget.write_cache(cache, readings)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
            self.assertEqual(widget.read_cache(cache), readings)

    def test_popup_marks_installed_plugins_without_quota(self):
        rows = widget.detail_lines({}, [("Sample plugin", "enabled")], 1795500000)
        self.assertIn("  Sample plugin (enabled) · quota unavailable", rows)


if __name__ == "__main__":
    unittest.main()
