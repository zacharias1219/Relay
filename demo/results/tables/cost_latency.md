| Policy | Backend | Calls | Prompt tokens | Completion tokens | List-price cost |
|---|---|---|---|---|---|
| P0 | groq-large | 88 | 17,209 | 3,332 | $0.0128 |
| P1 | groq-small | 97 | 20,746 | 3,983 | $0.00136 |
| P2 | groq-large | 49 | 10,961 | 2,380 | $0.00835 |
| P2 | groq-small | 40 | 6,455 | 971 | $0.000400 |
| P3 | groq-large | 13 | 4,499 | 773 | $0.00327 |
| P3 | groq-small | 28 | 9,184 | 1,559 | $0.000584 |
| P3 | local-small | 40 | 6,844 | 1,980 | $0 |

| Policy | Model time | Test time | Queue wait | Total list-price cost |
|---|---|---|---|---|
| P0 | 35.84 s | 2.99 s | 129.64 s | $0.0128 |
| P1 | 23.83 s | 3.42 s | 27.66 s | $0.00136 |
| P2 | 30.80 s | 3.20 s | 85.33 s | $0.00875 |
| P3 | 161.17 s | 3.90 s | 39.69 s | $0.00385 |
