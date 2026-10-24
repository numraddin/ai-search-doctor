# Cost-reduction experiment

Model gpt-6-luna; 5 independent runs per site per configuration; sites A (clean), B, C, D. Recall counts planted issues on B, C and D (145 checks per configuration). Cost uses OpenAI list prices $0.1/M input and $0.5/M output.

| Configuration | Tokens/audit | Tokens saved | Cost/audit | Cost saved | Recall | Recall lost (points) | Unexpected findings | Fixes written/audit |
|---|---|---|---|---|---|---|---|---|
| full | 3912 | 0% | $0.00097 | 0% | 94% (136/145) | 0.0 | 3 | 3.7 |
| trunc400 | 3405 | 13% | $0.00085 | 12% | 90% (130/145) | 4.1 | 0 | 3.1 |
| trunc200 | 3312 | 15% | $0.00084 | 13% | 79% (115/145) | 14.5 | 3 | 3.0 |
| nofix | 2423 | 38% | $0.00069 | 29% | 95% (138/145) | -1.4 | 1 | 0.0 |
| trunc400+nofix | 2286 | 42% | $0.00066 | 32% | 90% (131/145) | 3.4 | 4 | 0.0 |

## Missed planted issues by configuration (count of runs missed)

- **full**: b:no_pricing (1), d:hours_contradiction (3), d:unsupported_claims (5)
- **trunc400**: d:hours_contradiction (5), d:price_contradiction (5), d:unsupported_claims (5)
- **trunc200**: c:price_contradiction (5), d:founding_year_contradiction (5), d:hours_contradiction (5), d:price_contradiction (5), d:team_size_contradiction (5), d:unsupported_claims (5)
- **nofix**: b:no_pricing (1), d:hours_contradiction (1), d:unsupported_claims (5)
- **trunc400+nofix**: d:hours_contradiction (5), d:price_contradiction (5), d:unsupported_claims (4)

Notes: truncation only matters when a page is longer than the budget. The test pages are short (about 400 to 900 characters of text), so 400 and 200 are aggressive cuts relative to them; real pages are usually much longer, where a 5000-character budget already truncates. Fix-writer quality is not scored here, only whether fixes were produced.
