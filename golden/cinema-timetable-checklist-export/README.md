# Cinema evening timetable export

A small formatting task for the projection desk: `/app/timetable.py` exposes
`export_timetable(csv_text)`, which turns the evening's semicolon-delimited
screening CSV into the plain-text timetable described in `instruction.md`.
The pytest suite in `tests/` compares the returned report text exactly against
an independent reference on a fixed set of evening exports.
