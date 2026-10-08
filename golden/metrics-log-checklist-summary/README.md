# Server Metrics Log Summarizer

Parses a space-delimited server metrics log file and produces a per-server summary showing entry count, average value, and maximum value.

## Usage

Call `summarize_log(log_path)` from `solution.py` with the path to a log file. Returns a formatted summary string with one line per server.
