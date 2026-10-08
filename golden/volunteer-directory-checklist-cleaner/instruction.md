Clean the community volunteer directory CSV and produce a per-department summary report.

The CSV file is semicolon-delimited with columns: name, department, join_date, hours, donation. Leading and trailing whitespace in fields is stripped. Dates in the CSV use DD/MM/YYYY format. Rows where the department field is empty after stripping are assigned to the Outreach department. A missing hours value after stripping is treated as 0. Only rows with at least 10 hours are included in the summary.

The report lists each department with its volunteer count, total hours, average donation, and the month name of the earliest join date in that department. Averages are rounded half away from zero. Dollar amounts in the report use a comma as thousands separator. Names in the report are formatted in title case. Departments appear in the order they are first encountered in the input file. The report uses "1 volunteer" for a count of one and "N volunteers" for other counts. The returned string ends with a trailing newline.

The function signature is:
```python
def clean_directory(csv_path: str) -> str:
```

Example input (`data/example.csv`):
```
name;department;join_date;hours;donation
Alice Chen;Education;15/03/2024;20;150.00
Bob Park;Education;13/06/2024;30;200.00
Carol Wu;Safety;22/01/2024;15;75.50
Dave Kim;Safety;25/04/2024;25;125.00
```

Example output:
```
Volunteer Directory Summary
===========================

Education
  Volunteers: 2 volunteers
  Total hours: 50
  Avg donation: $175.00
  Earliest join month: March

Safety
  Volunteers: 2 volunteers
  Total hours: 40
  Avg donation: $100.25
  Earliest join month: January
```
