# Plant Nursery Price List Export

A utility that reads a semicolon-delimited plant catalog CSV and produces a formatted price list text report with per-category grouping, line totals, and a grand total.

## Usage

```python
from solution import export_price_list
report = export_price_list(csv_text)
print(report)
```

The input CSV has columns: code, name, category, price, stock, added_on.
