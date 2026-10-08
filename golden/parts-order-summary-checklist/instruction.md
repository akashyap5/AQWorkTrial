Write a function `export_order_summary(input_path, output_path)` that reads a parts order CSV and writes a formatted text summary.

The CSV columns are: part_id, description, quantity, unit_price, discount_pct, category, date_added.

Output a text file grouped by category. Categories are ELEC, MECH, HYDR, or MISC; an empty category field defaults to MISC. Show categories in the order ELEC, MECH, HYDR, MISC. Within each category, list items sorted by part_id descending. Output descriptions in uppercase. Items with quantity 100 or more are marked BULK after the pcs count. Format quantities with a comma thousands separator. An empty discount_pct field means 0 percent. Compute each line total as quantity × unit_price × (1 − discount_pct/100), rounding half-up to 2 decimal places. In subtotal and total lines, write "part" when the count is 1 and "parts" otherwise. The output file ends with exactly one newline.

Each item line has the format:
`  {part_id}: {DESCRIPTION}, {qty} pcs, ${unit_price} each, {disc}% disc, total: ${line_total}`

For bulk items, BULK appears after pcs (e.g., `1,200 pcs BULK`).

Subtotal lines: `  Subtotal: {count} part(s), ${amount}`
Total line: `TOTAL: {count} part(s), ${amount}`

The header is two lines: `ORDER SUMMARY` and `=============`.

A sample input is at /app/data/sample.csv.

Each category header line has the format: `{category}:`
