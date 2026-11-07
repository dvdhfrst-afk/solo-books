# The worked example

These figures were added up by hand from the CSV files in this folder, before any report was run.
`expected.json` holds the results and `tests/test_worked_example.py` checks the program against them.
Nothing here comes from the program's output. If a test disagrees with this page, the program is wrong
until the arithmetic here is shown to be wrong.

## Business A, first quarter of 2025

Rows after removing the overlap between the two checking exports (February appears in both):

### A Checking, opening balance 5,000.00

| Month | Rows | Net |
|---|---|---|
| January | +4,250.00, -850.00, -200.00, -12.00 | +3,188.00 |
| February | +3,100.50, -850.00, -75.25, -12.00 | +2,163.25 |
| March | +4,250.00, -850.00, -1,500.00, -312.47, -12.00, +1.37 | +1,576.90 |

Ending balances: 5,000.00 + 3,188.00 = 8,188.00; + 2,163.25 = 10,351.25; + 1,576.90 = 11,928.15.
The running Balance column in the bank's own export ends at the same figure.

### A Card, 200.00 owed at the start

| Month | Rows | Net |
|---|---|---|
| January | -18.40, -6.25, -6.25, -16.00, +200.00, -412.80 | -259.70 |
| February | -16.00, +112.80, -64.19 | +32.61 |
| March | -16.00, +312.47, -230.00, -150.00 | -83.53 |

The two 6.25 rows on January 9 are two real purchases and both must stay.

Amount owed: 200.00 + 259.70 = 459.70; - 32.61 = 427.09; + 83.53 = 510.62.

### Profit and loss

| Category | Working | Amount |
|---|---|---|
| 4000 Sales | 4,250.00 + 3,100.50 + 4,250.00 | 11,600.50 |
| 4900 Other income | 1.37 | 1.37 |
| **Total income** | | **11,601.87** |
| 6000 Advertising | 150.00 | 150.00 |
| 6080 Office expense | 64.19 | 64.19 |
| 6105 Office rent | 850.00 x 3 | 2,550.00 |
| 6130 Taxes and licenses | 75.25 | 75.25 |
| 6140 Travel | 412.80 - 112.80 + 230.00 | 530.00 |
| 6150 Meals | 18.40 + 6.25 + 6.25 | 30.90 |
| 6200 Software subscriptions | 16.00 x 3 | 48.00 |
| 6210 Bank fees | 12.00 x 3 | 36.00 |
| **Total expenses** | | **3,484.34** |
| **Net profit** | 11,601.87 - 3,484.34 | **8,117.53** |

By quarter this is all in Q1. By month:

| Month | Income | Expenses | Net |
|---|---|---|---|
| January | 4,250.00 | 850.00 + 12.00 + 30.90 + 16.00 + 412.80 = 1,321.70 | 2,928.30 |
| February | 3,100.50 | 850.00 + 75.25 + 12.00 + 16.00 - 112.80 + 64.19 = 904.64 | 2,195.86 |
| March | 4,251.37 | 850.00 + 12.00 + 16.00 + 230.00 + 150.00 = 1,258.00 | 2,993.37 |

Check: 2,928.30 + 2,195.86 + 2,993.37 = 8,117.53.

Kept out of profit: Transfer nets to zero (checking -512.47, card +512.47). Owner draws -1,500.00.

Check against cash: profit 8,117.53 - draws 1,500.00 + transfers 0.00 = 6,617.53.
Checking moved +6,928.15 and the card -310.62, which is also 6,617.53.

Balances, cash less what the card is owed: start 5,000.00 - 200.00 = 4,800.00; end 11,928.15 - 510.62 = 11,417.53, which
is 4,800.00 + 6,617.53. For February alone: start 8,188.00 - 459.70 = 7,728.30; end 10,351.25 - 427.09 = 9,924.16, which
is 7,728.30 + the month's net 2,195.86.

### Schedule C rollup, tax year 2025

| Line | Name | Amount |
|---|---|---|
| 1 | Gross receipts or sales | 11,600.50 |
| 3 | | 11,600.50 |
| 4 | Cost of goods sold | 0.00 |
| 5 | | 11,600.50 |
| 6 | Other income | 1.37 |
| 7 | Gross income | 11,601.87 |
| 8 | Advertising | 150.00 |
| 18 | Office expense | 64.19 |
| 20b | Other business property | 2,550.00 |
| 23 | Taxes and licenses | 75.25 |
| 24a | Travel | 530.00 |
| 24b | Deductible meals (full amount booked) | 30.90 |
| 27b | Other expenses, from line 48 | 84.00 |
| 48 | Software 48.00 + bank fees 36.00 | 84.00 |
| 28 | Total expenses | 3,484.34 |
| 29 | Tentative profit | 8,117.53 |
| 31 | Net profit | 8,117.53 |

## Business B, first quarter of 2025

B Checking opens at 1,000.00. The Pending row and the Total footer are not rows.

| Category | Working | Amount |
|---|---|---|
| 4000 Sales | 2,400.00 + 1,875.25 + 3,010.00 | 7,285.25 |
| 4100 Returns and allowances | 45.00 | -45.00 |
| **Total income** | | **7,240.25** |
| 5000 Purchases for resale | 980.00 + 1,200.00 | 2,180.00 |
| 5200 Materials | 210.40 | 210.40 |
| 5300 Other direct costs | 88.60 | 88.60 |
| **Cost of goods sold** | | **2,479.00** |
| **Gross profit** | 7,240.25 - 2,479.00 | **4,761.25** |
| 6200 Software subscriptions | 29.00 | 29.00 |
| **Net profit** | 4,761.25 - 29.00 | **4,732.25** |

Ending balance: 1,000.00 + 7,285.25 - 45.00 - 2,180.00 - 210.40 - 88.60 - 29.00 = 5,732.25.

Schedule C with inventory of 500.00 at the start of the year and 750.00 at the end:

| Line | Working | Amount |
|---|---|---|
| 1 | | 7,285.25 |
| 2 | | 45.00 |
| 3 | 7,285.25 - 45.00 | 7,240.25 |
| 35 | | 500.00 |
| 36 | | 2,180.00 |
| 38 | | 210.40 |
| 39 | | 88.60 |
| 40 | 500.00 + 2,180.00 + 210.40 + 88.60 | 2,979.00 |
| 41 | | 750.00 |
| 42 and 4 | 2,979.00 - 750.00 | 2,229.00 |
| 5 and 7 | 7,240.25 - 2,229.00 | 5,011.25 |
| 28 | | 29.00 |
| 31 | 5,011.25 - 29.00 | 4,982.25 |

Tie to profit and loss: 4,732.25 + inventory change 250.00 = 4,982.25.

## Reconciliations

A Checking ties each month with the balances worked out above.

A Card for March, against a statement that prints 427.09 owed at the start and 525.62 owed at the end.
The statement file carries one more line than the export: CITY PARKING GARAGE, -15.00, on March 27.

The books say 427.09 + 83.53 = 510.62 owed. The statement says 525.62. The difference is 15.00 more money out
on the statement than in the books. Adding the missing -15.00 row makes it tie, and adds 15.00 to the
books' Travel figure if booked there.
