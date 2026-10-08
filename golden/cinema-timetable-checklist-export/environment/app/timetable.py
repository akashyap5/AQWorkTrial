"""Cinema evening timetable exporter."""

import csv
import io
from datetime import date

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def export_timetable(csv_text: str) -> str:
    """Return the evening timetable text for the given CSV export contents."""
    raise NotImplementedError


if __name__ == "__main__":
    import sys

    with open(sys.argv[1]) as handle:
        sys.stdout.write(export_timetable(handle.read()))
