# Solver profile p017

Built from 243 recorded probes at 2026-10-07 16:29.

## Strengths
- Reliably implements entire methods from specification including complex algorithms like dynamic programming for route optimization (evidence: 20+ tasks)
- Employs extensive ad-hoc verification with inline Python test scripts covering edge cases, randomized differential testing, and example validation (evidence: 25+ tasks)
- Consistently compiles code before submission using py_compile (evidence: 20+ tasks)
- Achieves 4-5/5 on compound tasks with 10-11 independent formatting/computation rules including DD/MM/YYYY dates, half-up rounding, reverse-alphabetical tie-breaks, thousands separators, natural sort, title case, unit conversion (cents not dollars), and accounting formatting (parentheses for negatives) (evidence: 15+ tasks: Tasks 1,2,3,5,7,9,13,20,22,23,24,25,26,27,30)

## Weaknesses
- Overconfident self-assessment: routinely claims "all tests passed" or "verified" and submits despite missing bugs, even after writing extensive tests (evidence: 14 tasks: Tasks 4,6,7,8,10,11,15,16,17,18,21,22,28,29)
- Verification tests share blind spots with implementation: self-written tests encode the same rule misunderstandings, producing false confidence (evidence: 10 tasks: Tasks 4,6,8,10,11,15,16,17,21,28)
- Compound tasks with 12+ independent rules show variable performance with occasional 0/5 failures due to missed rules (evidence: 6 tasks: Tasks 4,11,16 at 0/5; Tasks 6,12,15 at 3-4/5)
- Strict inequality filtering (strictly greater than instead of greater than or equal to) causes failure because agent defaults to inclusive `>=` comparisons and its tests use boundary values that don't distinguish `>` from `>=` (evidence: 3 tasks: previous Tasks 10,28; new Task 17)

## Hypotheses
- Consecutive delimiters (e.g., `;;`) as empty fields cause failure when agent uses simple string splitting instead of proper CSV parsing, and its self-tests don't include consecutive delimiters → Design a task where input CSV has consecutive semicolons representing missing values that must be defaulted, with 10+ other standard formatting rules
- Inverted or non-standard value semantics (e.g., priority 5 is highest not lowest, value 5 means "standard" not "free") cause systematic failure because the agent defaults to programming conventions → Design a task where a priority field uses inverted scale (5=highest, 1=lowest) and items must be sorted by this priority
- Rules requiring semantic understanding of data processing interactions (e.g., normalization must occur before dedup, filtering affects which items appear in ordering) are more likely to be missed than mechanical formatting rules → Design a task with two versions: one with independent formatting rules and one with interacting semantic rules, measuring pass rate difference
- Multi-step computations where intermediate results must be stored and reused across processing stages are incorrectly implemented → Design a task where a discount threshold depends on the count of items passing a filter, and the discount applies to those same items after aggregation

## Guidance
Place difficulty in strict inequality boundaries (strictly greater than), consecutive delimiters as empty fields, inverted value semantics, and 12+ independent rules to overwhelm the agent's capacity. Combine tricky rules like strict inequalities or inverted priorities with 10+ standard formatting rules to maximize the chance the agent misses the critical non-default rule while passing its own ad-hoc tests. Do not rely on accounting formatting (parentheses for negatives), unit conversion (cents not dollars), or half-up rounding as primary difficulty drivers, as the agent handles these reliably even in compound tasks. The agent's self-assessment is unreliable proof of correctness; always verify with independent grading.
