Write a function `format_leaderboard(players, top_n)` in `/app/solution.py` that produces a competition leaderboard from player records.

Each player is a dict with keys `name` (string), `score` (float, int, or None), `reg_date` (string), and optionally `category` (string). Return a pipe-delimited string with a header line `rank|name|score|category`, one line per included player, and a footer line.

Players are sorted by score descending. Scores are rounded half-up to the nearest integer before ranking and display. When rounded scores are equal, the player with the earlier registration date ranks higher. Registration dates are in DD/MM/YYYY format. Player names appear in uppercase in the output. If the category key is absent, use "GENERAL" as the default. Scores of 1000 or more use a period as the thousands separator, for example 1.234 for one thousand two hundred thirty-four. A score of zero is displayed as a dash. If a player's score is None, treat it as zero. Tied scores receive the same rank number, and the next distinct score increments the rank by one. The output includes the first top_n players in ranking order; if the player at position top_n is tied on rounded score with the next player, all players sharing that score are included. The footer line reads "1 player ranked" for a single player or "N players ranked" otherwise. The output string ends with a newline.

Example:
```python
players = [
    {"name": "ALPHA", "score": 950, "reg_date": "15/03/2024", "category": "PRO"},
    {"name": "BETA", "score": 800, "reg_date": "20/01/2024", "category": "AMATEUR"},
    {"name": "GAMMA", "score": 700, "reg_date": "13/06/2024", "category": "PRO"},
]
print(format_leaderboard(players, 3))
```
Output:
```
rank|name|score|category
1|ALPHA|950|PRO
2|BETA|800|AMATEUR
3|GAMMA|700|PRO
3 players ranked
```
