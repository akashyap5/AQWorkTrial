# Parts Order Summary Exporter

Reads a parts order CSV file and produces a formatted text summary grouped by category with subtotals and a grand total.

## Usage

```python
from solution import export_order_summary
export_order_summary('input.csv', 'output.txt')
```

## CSV Format

Columns: part_id, description, quantity, unit_price, discount_pct, category, date_added

See /app/data/sample.csv for an example.
