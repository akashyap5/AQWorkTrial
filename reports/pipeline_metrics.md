# Pipeline metrics

Generated 2026-10-07T14:46:44 from batch state, the ledger, Task Lab job records, and Harbor results.

| Metric | Value |
|---|---|
| Task slots | 60 |
| Authored by GLM-5.1 | 48 |
| Pass structural validation | 48 |
| Functionally correct in Docker (reference passes, starter fails) | 47 |
| Oracle passes and no-op fails on the Task Lab worker | 31 |
| First 5-run probe (too_easy / learnable_range / too_hard / inconclusive) | {'too_easy': 12, 'learnable_range': 9, 'too_hard': 4} |
| Landed at 1-3/5 in some probe | 9 |
| Shipped after the GLM-5.1 fairness gate | 5 |
| GLM-5.1 spend attributed per task (ledger) USD | 0 |
| GLM-5.1 spend in the batch window, all calls (raw records) USD | 2.57 |
| GLM-5.3-flash solver spend USD | 0.76 |
| Median minutes per task (first to last event) | 13.4 |

## batch-20261007-134403-51289-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | receipt-formatter-checklist-24 | True | True | 5/5 | - | False | 0 | 0.0272 | 28.5 |
| 25 | shift-roster-checklist-export | True | True | 2/5, 2/5 | learnable | True | 0 | 0.054 | 50.4 |
| 26 | server-metrics-checklist-report | True | True | 0/5 | - | False | 0 | 0.026 | 8.9 |
| 27 | contact-list-cleaner-breadth | True | True | 1/5, 0/5, 4/4 | - | False | 0 | 0.074 | 54.7 |
| 28 | inventory-export-checklist | True | True | 0/5 | - | False | 0 | 0.0205 | 25.8 |
| 29 | leaderboard-checklist-format | True | True | 2/5 | learnable | True | 0 | 0.0357 | 48.5 |

## batch-20261007-134403-51290-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | receipt-formatter-checklist | True | True | 5/5 | - | False | 0 | 0.0324 | 28.8 |
| 25 | roster-export-checklist-breadth | True | True | 4/5 | - | False | 0 | 0.0285 | 13.4 |
| 26 | metrics-log-checklist-summary | True | True | 3/5 | learnable | True | 0 | 0.046 | 49.7 |
| 27 | contact-list-cleaner-checklist | True | True | 0/5 | - | False | 0 | 0.0556 | 28.1 |
| 28 | catalog-export-checklist | True | True | 3/5 | learnable | True | 0 | 0.0341 | 47.8 |
| 29 | sales-leaderboard-checklist | True | True | 2/5, 5/5 | held_by_fairness_audit | False | 0 | 0.0562 | 54.9 |

## batch-20261007-140247-94412-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | print-shop-invoice-checklist | True | True | 5/5 | - | False | 0 | 0.0108 | 32.3 |
| 25 | workshop-timetable-checklist-export | True | True | 2/5 | learnable | True | 0 | 0.0186 | 33.0 |
| 26 | parking-garage-checklist-report | True | True | 5/5 | - | False | 0 | 0.0322 | 32.4 |
| 27 | subscriber-list-checklist-cleaner | True | True | 0/5 | - | False | 0 | 0.0149 | 28.6 |
| 28 | wholesale-pricelist-checklist | True | True | 4/5 | - | False | 0 | 0.0196 | 32.4 |
| 29 | tournament-leaderboard-checklist | True | True | 4/5 | - | False | 0 | 0.0156 | 11.7 |

## batch-20261007-140247-94413-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | dry-clean-receipt-checklist | True | True | 5/5 | - | False | 0 | 0.0143 | 32.4 |
| 25 | fleet-maintenance-checklist-export | True | True | 5/5 | - | False | 0 | 0.0169 | 32.5 |
| 26 | ticket-resolution-checklist-report | True | True | 3/5, 5/5 | held_by_fairness_audit | False | 0 | 0.0335 | 35.7 |
| 27 | subscriber-list-checklist | True | True | 4/5 | - | False | 0 | 0.013 | 32.0 |
| 28 | parts-order-summary-checklist | True | True | 1/5, 0/0 | held_by_fairness_audit | False | 0 | 0.016 | 30.9 |
| 29 | step-challenge-leaderboard-checklist | True | False | - | invalid | False | 0 | 0.0 | 2.1 |

## batch-20261007-141923-24326-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | equipment-rental-invoice-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 1.8 |
| 25 | library-schedule-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 1.4 |
| 26 | bakery-order-checklist-summary | True | False | 0/0 | - | False | 0 | 0.0 | 1.3 |
| 27 | mailing-list-cleaner-breadth | True | True | 4/4 | - | False | 0 | 0.0124 | 20.2 |
| 28 | seed-catalog-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 1.3 |
| 29 | charity-leaderboard-checklist | True | True | 0/0 | - | False | 0 | 0.0 | 0.7 |

## batch-20261007-141923-24328-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | repair-shop-invoice-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 1.3 |
| 25 | pool-booking-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 1.3 |
| 26 | gym-attendance-checklist-report | True | False | 0/0 | - | False | 0 | 0.0 | 1.3 |
| 27 | mailing-list-summary-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 1.4 |
| 28 | tool-rental-invoice-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 0.5 |
| 29 | charity-run-leaderboard-checklist | True | True | 0/0 | - | False | 0 | 0.0 | 0.6 |

## batch-20261007-141923-24330-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | catering-receipt-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 0.7 |
| 25 | community-center-schedule-checklist | True | True | 5/5 | - | False | 0 | 0.0213 | 19.3 |
| 26 | bakery-production-checklist-report | True | False | 0/0 | - | False | 0 | 0.0 | 1.7 |
| 27 | newsletter-subscriber-checklist-cleaner | True | True | 0/0 | - | False | 0 | 0.0 | 0.6 |
| 28 | wholesale-pricelist-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 1.5 |
| 29 | fundraiser-leaderboard-checklist | True | False | 0/0 | - | False | 0 | 0.0 | 1.1 |

## batch-20261007-141923-24331-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 24 | bike-repair-invoice-checklist | True | True | 0/1 | - | False | 0 | 0.0033 | 0.5 |
| 25 | swim-schedule-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 0.2 |
| 26 | wifi-usage-checklist-report | True | False | 0/0 | - | False | 0 | 0.0 | 1.4 |
| 27 | mailing-list-report-checklist | True | True | 0/0 | - | False | 0 | 0.0 | 0.7 |
| 28 | bookstore-pricelist-checklist-export | True | False | 0/0 | - | False | 0 | 0.0 | 2.0 |
| 29 | gym-leaderboard-checklist | True | True | 4/5 | - | False | 0 | 0.0257 | 19.8 |

## batch-20261007-144136-62036-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 34 | (not authored) | - | - | - | - | False | - | - | - |
| 35 | (not authored) | - | - | - | - | False | - | - | - |
| 36 | (not authored) | - | - | - | - | False | - | - | - |
| 37 | (not authored) | - | - | - | - | False | - | - | - |
| 38 | (not authored) | - | - | - | - | False | - | - | - |
| 39 | (not authored) | - | - | - | - | False | - | - | - |

## batch-20261007-144136-62037-0

| Slot | Task | Structural | Correct | Probes (passes/5) | Gate | Shipped | GLM $ | Solver $ | Min |
|---|---|---|---|---|---|---|---|---|---|
| 38 | (not authored) | - | - | - | - | False | - | - | - |
| 39 | (not authored) | - | - | - | - | False | - | - | - |
| 40 | (not authored) | - | - | - | - | False | - | - | - |
| 41 | (not authored) | - | - | - | - | False | - | - | - |
| 42 | (not authored) | - | - | - | - | False | - | - | - |
| 43 | (not authored) | - | - | - | - | False | - | - | - |
