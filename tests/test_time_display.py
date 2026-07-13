from datetime import UTC, datetime

from job_discovery.time_display import format_eastern_time


def test_format_eastern_time_observes_daylight_saving_time():
    summer = datetime(2026, 7, 13, 19, 30, tzinfo=UTC)
    winter = datetime(2026, 1, 13, 19, 30, tzinfo=UTC)

    assert format_eastern_time(summer) == "2026-07-13 03:30 PM EDT"
    assert format_eastern_time(winter) == "2026-01-13 02:30 PM EST"


def test_format_eastern_time_treats_naive_database_values_as_utc():
    stored_value = datetime(2026, 7, 13, 19, 30)

    assert format_eastern_time(stored_value) == "2026-07-13 03:30 PM EDT"
    assert format_eastern_time(None) == ""
