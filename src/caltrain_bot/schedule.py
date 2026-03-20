from pathlib import Path

import pygtfs
import sqlparse
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
            self.schedule.session.execute(text(statement))
        self.schedule.session.commit()

    def get_agencies(self):
        """Returns a list of all agencies in the schedule."""
        rows = self.schedule.session.execute(
            text("""
            SELECT agency_id, agency_name
            FROM agency
            ORDER BY agency_name
        """)
        ).fetchall()

        for row in rows:
            print(row.agency_id, row.agency_name)
