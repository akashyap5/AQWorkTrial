Write a function `export_price_list(csv_text: str) -> str` that processes a product catalog CSV and produces a formatted price list.

The CSV has a header row with columns: sku, name, category, price, quantity, added_date. The price is in dollars and may include fractional cents. The added_date column uses DD/MM/YYYY format. If the category field is empty, treat the product as "Uncategorized". If the quantity field is empty, default to 1.

Only include products priced at $10.00 or more. For each product, compute the line total as price times quantity, rounding half-up to the nearest cent.

Group products by category. Categories appear in this order: Electronics, Clothing, Food, Books, Uncategorized. Within each category, sort by price descending, then by name alphabetically for ties.

Product names in the output should be in title case. Dollar amounts over $999.99 should include comma thousands separators. Dates should be formatted as "Mon DD, YYYY".

Example output (single category):

== Electronics ==
  SKU-001: Widget Pro, $25.00 x 3 = $75.00, added Jan 15, 2024
  Subtotal: $75.00

== Summary ==
Total: $75.00 (3 items)

Each category section is followed by a blank line. The summary uses "item" for a count of 1 and "items" otherwise. The output must end with a newline character.
