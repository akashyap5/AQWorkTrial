Write a function `clean_roster(csv_text: str) -> str` that processes a community garden plot assignment roster and produces a formatted text directory.

The input is a semicolon-delimited CSV with columns: `plot_id;gardener_name;email;joined_date;annual_fee_cents;region;status`. The first row is the header. Dates in the input use DD/MM/YYYY format. The annual_fee_cents column stores fees in cents. Skip any rows where the email field is empty. Gardeners with an empty status field default to "active".

Gardener names should appear in title case in the output. Group gardeners by region, listing regions in the order they first appear in the input. Each region group begins with a line containing just the region name. Within each region, sort gardeners by join date (earliest first), then alphabetically by name for ties.

Each gardener line shows: `PlotID - Name - Y year/years - Status` where Y is the number of calendar years of membership, counting both the join year and 2025 inclusively. Use "year" for 1 and "years" otherwise.

After all region groups, output a summary line: `Total: N gardener/gardeners, $X in fees` where N uses "gardener" for 1 and "gardeners" otherwise, and X is the total fees in dollars rounded half-up to the nearest whole dollar with thousands separator commas. The output must end with exactly one trailing newline.
