# Cinema evening timetable export

The projection desk receives tonight's screening list as a CSV export and needs it
turned into a plain-text timetable for the front desk. Implement
`export_timetable(csv_text: str) -> str` in `/app/timetable.py`; it takes the file
contents and returns the finished report as a string.

The export is semicolon-delimited with a header row and the columns
`date;title;screen;start;duration;capacity;sold`. The remaining values are plain
integers, times are zero-padded 24-hour `HH:MM`, and a file is a single evening, so
every row carries the same screening date, written day-first as DD/MM/YYYY. Fields
can arrive with padding, so trim surrounding whitespace from every value before
use; a row whose title is blank after trimming is dropped from the report, and a
screening with an empty duration field runs 90 minutes.

The report opens with `Timetable for {weekday} {date}`, where {weekday} is the
English weekday name of the screening date and {date} is the date as written in
the file. One line follows per screening, ordered by start time with ties going to
the higher screen number, each reading
`{start}  Screen {n}  {title}  {duration} min  {pct}%` with two spaces between
fields, and a screening starting at 20:00 or later ends with two spaces and the
word `late`. The percentage is seats sold as a share of capacity, rounded to a
whole percent with halves rounding up. After one blank line the report closes with
`Seats sold: {total} across {count} screening{s}`, where the total uses thousands
separators and the plural form is used for every count except one. The report ends
with a single newline character after the last line.

For example, the export

    date;title;screen;start;duration;capacity;sold
    23/01/2025;Stalker;2;18:30;160;80;60
    23/01/2025;Heat;1;21:15;89;60;42

yields

    Timetable for Thursday 23/01/2025
    18:30  Screen 2  Stalker  160 min  75%
    21:15  Screen 1  Heat  89 min  70%  late

    Seats sold: 102 across 2 screenings
