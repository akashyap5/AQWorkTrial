# Shift Roster Export

Write a function `export_timetable(csv_data: str, week_start: str, week_end: str) -> str` that produces a weekly shift summary from a CSV of shift records.

The CSV has a header row followed by data rows with columns: `staff_id,name,department,shift_type,hours,date`. Dates in the CSV are in DD/MM/YYYY format. If the shift_type field is empty, treat it as "Regular".

Filter rows to those where the date falls between week_start and week_end inclusive. Group the matching rows by department. Departments should appear in this order: Operations, Sales, Engineering, HR, Finance, Other. Any department not in that list is grouped under "Other". Department names are matched against that list ignoring case and are displayed exactly as written in the list. When no department has a shift in the range, there is a single blank line between the Timetable line and the Total line.

For each department, report the number of shifts, total hours as an integer with comma thousands separator, and average hours per shift rounded half-up to one decimal place. Use "shift" for a count of 1 and "shifts" for any other count. Only show departments that have at least one shift in the range.

The output format is:

```
Timetable: <week_start> to <week_end>

<department>: <count> shift(s), <total> hours (avg <average>)
...

Total: <count> shift(s), <total> hours
```

The output ends with a newline character.

### Example

Input:
```csv
staff_id,name,department,shift_type,hours,date
001,Alice,Operations,Morning,8,04/04/2024
002,Bob,Operations,Evening,7,04/04/2024
003,Carol,Sales,Morning,9,04/04/2024
004,Dave,Sales,Evening,8,04/04/2024
005,Eve,Operations,Night,8,04/04/2024
```

With week_start = "04/04/2024" and week_end = "04/04/2024", the output is:

```
Timetable: 04/04/2024 to 04/04/2024

Operations: 3 shifts, 23 hours (avg 7.7)
Sales: 2 shifts, 17 hours (avg 8.5)

Total: 5 shifts, 40 hours
```
