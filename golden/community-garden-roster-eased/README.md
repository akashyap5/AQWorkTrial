# Community Garden Roster Cleaner

Implement `clean_roster(csv_text: str) -> str` to process a semicolon-delimited CSV roster and produce a formatted text directory.

## Input Format

Semicolon-delimited CSV with columns: `plot_id;gardener_name;email;joined_date;annual_fee_cents;region;status`. The first row is the header. Dates use DD/MM/YYYY format. Fees are in cents.

## Rules

- Skip rows with an empty email field.
- Empty status defaults to "active".
- Names appear in title case in the output.
- Group by region in order of first appearance. Each group starts with a line containing just the region name.
- Within a region, sort by join date (earliest first), then alphabetically by name.
- Years of membership: count both the join year and 2025 inclusively. Use "year" for 1, "years" otherwise.
- Summary line: `Total: N gardener/gardeners, $X in fees` with singular/plural forms, X rounded half-up to whole dollars with thousands-separator commas.
- Output ends with exactly one trailing newline.

## Output Line Formats

- Region header: just the region name
- Gardener: `PlotID - Name - Y year/years - Status`
- Summary: `Total: N gardener/gardeners, $X in fees`
