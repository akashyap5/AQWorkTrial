Write a function `export_schedule(csv_path, output_path)` that reads a library program session CSV and writes a formatted summary report.

The CSV has columns: program, date, start_time, duration_min, leader, seats, registered, fee_cents. Dates in the CSV are in DD/MM/YYYY format. The fee_cents column contains amounts in cents. If the seats field is empty, default to 30. A leader field that is empty or contains only whitespace displays as "VOLUNTEER".

Group sessions by program name, normalizing to title case for both grouping and display. For each program, compute: the session count, the earliest session date in YYYY-MM-DD format, total registered, average fill rate as a percentage (total registered divided by total seats times 100) rounded half up to the nearest whole number, and total revenue in dollars (sum of registered times fee_cents per session, divided by 100). List all unique leaders for each program in alphabetical order.

Sort programs by total registered descending; ties are broken by program name in reverse alphabetical order. Use "session" for one session and "sessions" for multiple. Format dollar amounts with a comma thousands separator and two decimal places. The output file must end with a newline character. If the CSV has no data rows, the output file contains only a newline.

Each output line uses this format:
NAME: COUNT session/sessions, first DATE, TOTAL registered, FILL% fill, $REVENUE, Leader: LEADERS
