| Policy | Backend | Calls | Prompt tokens | Completion tokens | List-price cost |
|---|---|---|---|---|---|
| P0 | groq-large | 88 | 17,145 | 29,855 | $0.0205 |
| P1 | groq-small | 103 | 21,918 | 30,083 | $0.0107 |
| P2 | groq-large | 45 | 9,720 | 15,633 | $0.0108 |
| P2 | groq-small | 40 | 6,455 | 11,431 | $0.00391 |
| P3 | groq-large | 15 | 4,875 | 5,511 | $0.00404 |
| P3 | groq-small | 28 | 9,184 | 8,805 | $0.00333 |
| P3 | local-small | 40 | 6,844 | 1,980 | $0 |

| Policy | Model time | Test time | Queue wait | Total list-price cost |
|---|---|---|---|---|
| P0 | 139.55 s | 3.49 s | 129.26 s | $0.0205 |
| P1 | 65.15 s | 4.17 s | 33.94 s | $0.0107 |
| P2 | 97.49 s | 3.50 s | 97.18 s | $0.0148 |
| P3 | 190.77 s | 5.19 s | 31.13 s | $0.00737 |
