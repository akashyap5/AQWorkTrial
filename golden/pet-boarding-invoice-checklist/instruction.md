Write a function `render_invoice(csv_path)` in `/app/solution.py` that reads a pet boarding kennel CSV and returns a formatted invoice string.

The CSV is semicolon-delimited with columns: pet_name, breed, weight_kg, owner, check_in, check_out, pickup_time, nightly_rate. Dates are DD/MM/YYYY. Pickup times are HH:MM in 24-hour format.

For each pet, compute nights as checkout minus checkin in days. The base charge is nightly rate × nights, rounded to the nearest cent with halves rounding up. A weight surcharge of $4 per night applies when weight exceeds 25 kg. A late pickup fee of $20 applies when pickup time is after 17:00. Pet total is base charge + weight surcharge + late fee.

If weight_kg is empty, use 10.0 kg. If breed is empty, display "Mixed breed". Owner names are printed in uppercase. Sort pets by total descending; break ties by pet name in reverse alphabetical order. Format amounts with comma thousands separators. Use "night" for one night, "nights" otherwise. The output ends with a newline.

Output format:
```
KENNEL INVOICE

Pet: <name>
Owner: <OWNER>
Breed: <breed>
Weight: <w> kg
Stay: <in> - <out> (<N> night[s])
Charges: Rate $<base> + Weight $<ws> + Late $<lf> = $<total>

---
Total: $<grand>
```
A blank line separates each pet block and precedes the total line. A sample CSV is in `/app/data/sample.csv`.
