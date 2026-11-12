| Policy | Backend | Calls | Prompt tokens | Completion tokens | List-price cost |
|---|---|---|---|---|---|
| P0 | groq-large | 91 | 30,463 | 14,029 | $0.0130 |
| P1 | groq-small | 89 | 28,087 | 11,774 | $0.00564 |
| P2 | groq-large | 48 | 17,425 | 6,662 | $0.00661 |
| P2 | groq-small | 40 | 10,134 | 5,181 | $0.00231 |
| P3 | groq-large | 3 | 1,334 | 931 | $0.000759 |
| P3 | groq-small | 13 | 5,586 | 3,582 | $0.00149 |
| P3 | local-small | 40 | 6,844 | 2,057 | $0 |

| Policy | Model time | Test time | Queue wait | Total list-price cost |
|---|---|---|---|---|
| P0 | 69.61 s | 4.00 s | 293.77 s | $0.0130 |
| P1 | 56.80 s | 3.68 s | 222.91 s | $0.00564 |
| P2 | 56.97 s | 3.91 s | 142.70 s | $0.00893 |
| P3 | 798.20 s | 3.83 s | 0.00 s | $0.00225 |
