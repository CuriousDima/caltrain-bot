from functools import cached_property
from pathlib import Path

import pygtfs
import sqlparse
from loguru import logger
from sqlalchemy import text


def preprocess_schedule(schedule: pygtfs.Schedule, preprocess_sql: Path) -> None:
    """Preprocesses the schedule data using the provided SQL."""
    # Execute preprocessing SQL
    if not preprocess_sql.exists():
        raise FileNotFoundError(
            f"Preprocessing SQL file not found at: {preprocess_sql}"
        )
    with open(preprocess_sql, "r") as f:
        sql = f.read()
    # Split the SQL into individual statements and execute them
    statements = sqlparse.split(sql)
    for statement in statements:
        logger.info(f"Executing SQL statement: {statement[:100]}...")
        schedule.session.execute(text(statement))
        schedule.session.commit()


class ScheduleManager:
    """Manages the GTFS schedule and provides methods to query train times."""

    def __init__(
        self,
        schedules_file: Path,
        preprocess_sql: Path,
        use_in_memory_db: bool = True,
    ) -> None:
        """Initialize the GTFS schedule manager.

        Args:
            schedules_file: Path to the GTFS archive to load.
            preprocess_sql: Path to the SQL script used to preprocess the schedule.
            use_in_memory_db: When ``True``, load the schedule into an in-memory
                database. When ``False``, use the on-disk ``schedule.db`` file.
                The on-disk database should only be used during development and
                treated as a debug mode cache.
        """
        if use_in_memory_db:
            db_path = ":memory:"
            should_initialize = True
        else:
            db_file_path = Path(__file__).parent.parent.parent / "data" / "schedule.db"
            if db_file_path.exists():
                logger.info(f"Loading existing schedule database from: {db_file_path}")
                should_initialize = False
            else:
                logger.info(f"Creating new schedule database at: {db_file_path}")
                should_initialize = True
            db_path = str(db_file_path)

        self.schedule: pygtfs.Schedule = pygtfs.Schedule(db_path)

        if should_initialize:
            pygtfs.append_feed(self.schedule, schedules_file)
            preprocess_schedule(self.schedule, preprocess_sql)

    @cached_property
    def stations(
        self,
    ) -> tuple[str, ...]:  # variable-length tuple where every item is a str
        """Returns a list of all stations in the schedule."""
        return tuple(
            # scalars() unwraps the first column of each row,
            # so we get a flat list of station names
            self.schedule.session.scalars(
                # use ORDER BY for deterministic output
                text("""
                    SELECT DISTINCT origin_station_query_name
                    FROM train_station_journeys
                    ORDER BY origin_station_query_name
                """)
            )
        )
