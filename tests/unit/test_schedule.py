from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from caltrain_bot.schedule import ScheduleManager


@pytest.fixture(scope="module")
def schedule_manager():
    gtfs_path = Path(__file__).parent.parent.parent / "data" / "caltrain-ca-us.zip"
    sql_path = Path(__file__).parent.parent.parent / "sql" / "train_stop_timeline.sql"
    manager = ScheduleManager(schedules_file=gtfs_path, preprocess_sql=sql_path)
    yield manager
    manager.schedule.session.close()


def test_schedule_manager_initialization(schedule_manager):
    assert schedule_manager is not None


def test_get_stations(schedule_manager):
    assert schedule_manager.stations == (
        "22nd street",
        "bayshore",
        "belmont",
        "blossom hill",
        "broadway",
        "burlingame",
        "california avenue",
        "capitol",
        "college park",
        "gilroy",
        "hayward park",
        "hillsdale",
        "lawrence",
        "menlo park",
        "millbrae",
        "morgan hill",
        "mountain view",
        "palo alto",
        "redwood city",
        "san antonio",
        "san bruno",
        "san carlos",
        "san francisco",
        "san jose diridon",
        "san martin",
        "san mateo",
        "santa clara",
        "south san francisco",
        "sunnyvale",
        "tamien",
    )


def test_get_trains_normalizes_aware_datetimes_to_caltrain_wall_time(
    schedule_manager,
):
    departure_times = (
        datetime(2026, 8, 24, 18),
        datetime(2026, 8, 24, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
        datetime(2026, 8, 25, 1, tzinfo=ZoneInfo("UTC")),
    )
    expected_departures = (
        "2026-08-24 17:43:00",
        "2026-08-24 17:55:00",
        "2026-08-24 18:10:00",
        "2026-08-24 18:25:00",
    )

    for departure_time in departure_times:
        trains = schedule_manager.get_trains(
            departure_station_query_name="palo alto",
            arrival_station_query_name="san francisco",
            departure_time=departure_time,
        )

        assert tuple(str(train.origin_departure_timestamp) for train in trains) == (
            expected_departures
        )
