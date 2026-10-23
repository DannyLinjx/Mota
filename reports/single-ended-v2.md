# Routing benchmark — single-ended-v2

Generated: 2026-10-22T01:53:08.312724+00:00

- cases: **6**
- requested nets (frozen denominator): **8**
- completed: **8**
- unrouted (kept in denominator): **0**
- U total: **0**
- frozen hashes: **ok**
- status counts: {'SUCCEEDED': 6}

| case | status | requested | completed | unrouted | U | wirelength mm | vias | runtime ms | frozen |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| two_layer_three_terminal | SUCCEEDED | 1 | 1 | 0 | 0 | 45.00 | 0 | 0.6 | ok |
| two_layer_obstacle | SUCCEEDED | 1 | 1 | 0 | 0 | 33.00 | 0 | 0.3 | ok |
| two_layer_two_nets | SUCCEEDED | 2 | 2 | 0 | 0 | 50.00 | 2 | 0.4 | ok |
| four_layer_smd | SUCCEEDED | 1 | 1 | 0 | 0 | 30.00 | 2 | 0.6 | ok |
| six_layer_smd | SUCCEEDED | 1 | 1 | 0 | 0 | 30.00 | 2 | 0.8 | ok |
| locked_track | SUCCEEDED | 2 | 2 | 0 | 0 | 40.00 | 0 | 0.2 | ok |

Unrouted nets are never removed from the denominator. INFEASIBLE is a finite-grid proof; UNKNOWN/PARTIAL means the budget was exhausted.
