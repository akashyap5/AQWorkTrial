Write a function `export_timetable(sessions, start_date, end_date)` in `/app/solution.py` that produces a formatted workshop timetable.

Each session dict has keys: course (str), date (str), start_time (str in HH:MM), duration (float in hours), instructor (str), room (str), fee (int in dollars), capacity (int). Dates use DD/MM/YYYY format. Include sessions whose date falls on or between start_date and end_date inclusive.

Sort matching sessions by date ascending, then start_time ascending, then course name in reverse alphabetical order. Course names should be normalised to title case. Duration values are rounded to one decimal place with half-up rounding. Fees are displayed with a comma thousands separator. An empty instructor field displays as "TBD". An empty room field displays as "Online". A capacity of 0 displays as "Unlimited".

Each output line uses the format: `{course} | {date} | {start_time} | {duration}h | {instructor} | {room} | ${fee} | {capacity}`

After all session lines, add a blank line then `Total: N session` when N is 1, or `Total: N sessions` otherwise. The output must end with a trailing newline.
