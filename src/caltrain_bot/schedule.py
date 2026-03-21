from functools import cached_property
from pathlib import Path

import pygtfs
import sqlparse
from loguru import logger
from sqlalchemy import text


class ScheduleManager:
    """Manages the GTFS schedule and provides methods to query train times."""

    def __init__(self, schedules_file: Path, preprocess_sql: Path):
        self.schedule: pygtfs.Schedule = pygtfs.Schedule(":memory:")

        # Load GTFS data into the in-memory schedule
        pygtfs.append_feed(self.schedule, schedules_file)

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
            logger.info(f"Executing SQL statement: {statement}")
            self.schedule.session.execute(text(statement))
        self.schedule.session.commit()

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
