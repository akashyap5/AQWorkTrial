Parse the server metrics log at the given path and return a per-server summary string.

The log is space-delimited with a header row followed by data rows. Columns are: date, server, metric, value, severity. Dates follow DD/MM/YYYY format. Consecutive spaces indicate empty fields. Server names are case-sensitive. Missing severity values default to WARN. Include only entries dated from 01/06/2024 through 31/12/2024 inclusive with severity of WARN or higher (WARN, ERROR, CRIT). Values may contain commas as thousands separators. For each server, compute the count, average, and maximum of the values. Averages round half-up to one decimal place. Sort output lines by server name in natural order (e.g., server2 before server10). Use "entry" for a count of 1 and "entries" otherwise. The output must not end with a trailing newline.

Each output line has the format: SERVER: N entry/entries, avg=A, max=M

The function signature is: def summarize_log(log_path: str) -> str
