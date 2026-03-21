from pathlib import Path

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
