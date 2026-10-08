# Pub Quiz League Leaderboard

Write `format_leaderboard(csv_text)` in `/app/solution.py` that reads a semicolon-delimited CSV of pub quiz results and returns a formatted leaderboard string.

The CSV has a header row with columns: `player;team;score;events;joined`. The `joined` column is a date in DD/MM/YYYY format. Players with at least 2 events are included in the leaderboard. The leaderboard is sorted by score descending; when scores are tied, the player who appears earlier in the input ranks higher.

Player names should be stripped of surrounding whitespace and shown in uppercase. If the team field is empty, display "UNAFFILIATED" as the team name. The average score per event is score divided by events, rounded to one decimal place using half-up rounding. Scores are displayed with thousands separators (e.g., 1,250). Use "pt" for a score of 1 and "pts" otherwise. Each line begins with the rank number followed by its ordinal suffix (1st, 2nd, 3rd, 4th, 11th, 21st, etc.). The joined date is reformatted as "DD Mon YYYY" (e.g., "07 Mar 2024").

Each output line follows this format:
`{rank}{suffix} {PLAYER} | {TEAM} | {score} {pt/pts} | avg {average} | joined {date}`

If no players qualify, return "No qualifiers." followed by a newline. The output always ends with a trailing newline.

Example:
Input:
```
player;team;score;events;joined
Alice;Quiztastic;100;4;07/07/2024
Bob;Brainiacs;80;4;07/07/2024
```
Output:
```
1st ALICE | Quiztastic | 100 pts | avg 25.0 | joined 07 Jul 2024
2nd BOB | Brainiacs | 80 pts | avg 20.0 | joined 07 Jul 2024
```
