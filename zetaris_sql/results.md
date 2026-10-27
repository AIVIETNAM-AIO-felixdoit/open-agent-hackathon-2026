# Zetaris proof queries

Run with `python zetaris_queries.py` on 2026-10-28 02:52 against a local Zetaris platform. Every query below ran live; results are pasted as returned.


## Source 1: SEC_DATA (EDGAR XBRL facts)

### Rivian annual revenue straight from the raw REST table

```sql
SELECT f.`end` AS period_end, f.val AS revenue_usd, f.form, f.filed FROM sec_data.rivian_revenue_raw LATERAL VIEW explode(units.USD) AS f WHERE f.form = '10-K' AND f.fp = 'FY' ORDER BY f.filed DESC LIMIT 6
```

| period_end | revenue_usd | form | filed |
|---|---|---|---|
| 2023-12-31 | 4434000000 | 10-K | 2026-02-12 |
| 2024-12-31 | 4970000000 | 10-K | 2026-02-12 |
| 2025-12-31 | 5387000000 | 10-K | 2026-02-12 |
| 2022-12-31 | 1658000000 | 10-K | 2025-02-24 |
| 2023-12-31 | 4434000000 | 10-K | 2025-02-24 |
| 2024-12-31 | 4970000000 | 10-K | 2025-02-24 |

### Rivian cash on hand, every filing

```sql
SELECT f.`end` AS period_end, f.val AS cash_usd, f.form FROM sec_data.rivian_cash_raw LATERAL VIEW explode(units.USD) AS f ORDER BY f.`end` DESC LIMIT 8
```

| period_end | cash_usd | form |
|---|---|---|
| 2026-06-30 | 3592000000 | 10-Q |
| 2026-03-31 | 2845000000 | 10-Q |
| 2025-12-31 | 3579000000 | 10-K |
| 2025-12-31 | 3579000000 | 10-Q |
| 2025-12-31 | 3579000000 | 10-Q |
| 2025-09-30 | 4441000000 | 10-Q |
| 2025-06-30 | 4812000000 | 10-Q |
| 2025-03-31 | 4693000000 | 10-Q |

### Lucid net income by fiscal year

```sql
SELECT f.fy, f.`end` AS period_end, f.val AS net_income_usd FROM sec_data.lucid_net_income_raw LATERAL VIEW explode(units.USD) AS f WHERE f.form = '10-K' AND f.fp = 'FY' ORDER BY f.filed DESC LIMIT 6
```

| fy | period_end | net_income_usd |
|---|---|---|
| 2025 | 2023-12-31 | -2828420000 |
| 2025 | 2024-12-31 | -2713942000 |
| 2025 | 2025-12-31 | -2698051000 |
| 2024 | 2023-12-31 | -2828420000 |
| 2024 | 2022-12-31 | -1304460000 |
| 2024 | 2024-12-31 | -2713942000 |


## Source 2: SEC_PROFILES (EDGAR submissions)

### Company profile fields

```sql
SELECT name, cik, sic, `sicDescription`, `stateOfIncorporation`, `fiscalYearEnd`, tickers[0] AS ticker FROM sec_profiles.rivian_submissions_raw
```

| name | cik | sic | sicDescription | stateOfIncorporation | fiscalYearEnd | ticker |
|---|---|---|---|---|---|---|
| Rivian Automotive, Inc. / DE | 0001874178 | 3711 | Motor Vehicles & Passenger Car Bodies | DE | 1231 | RIVN |

### Most recent Rivian filings (form types)

```sql
SELECT form, filed FROM (SELECT posexplode(filings.recent.form) AS (i, form), filings.recent.filingDate AS dates FROM sec_profiles.rivian_submissions_raw) x LATERAL VIEW explode(array(dates[i])) AS filed ORDER BY filed DESC LIMIT 8
```

| form | filed |
|---|---|
| 4 | 2026-10-22 |
| 4 | 2026-10-22 |
| 4 | 2026-10-22 |
| 4 | 2026-10-22 |
| 8-K | 2026-10-07 |
| 4 | 2026-10-02 |
| 8-K | 2026-10-02 |
| 144 | 2026-10-01 |


## Source 3: COMPANY_DNS (SIC hierarchy)

### SIC codes loaded

```sql
SELECT COUNT(*) AS sic_codes FROM company_dns.sic_codes_raw LATERAL VIEW explode(from_json(to_json(data.sics), 'map<string, struct<description:string>>')) AS code, v
```

| sic_codes |
|---|
| 1005 |

### Transportation-equipment codes around the EV industry

```sql
SELECT sic_code, description, major_group_desc FROM lightning.metastore.dd.dd_usl.sic_code WHERE sic_code LIKE '37%' ORDER BY sic_code LIMIT 10
```

| sic_code | description | major_group_desc |
|---|---|---|
| 3711 | Motor Vehicles and Passenger Car Bodies | Transportation Equipment |
| 3713 | Truck and Bus Bodies | Transportation Equipment |
| 3714 | Motor Vehicle Parts and Accessories | Transportation Equipment |
| 3715 | Truck Trailers | Transportation Equipment |
| 3716 | Motor Homes | Transportation Equipment |
| 3721 | Aircraft | Transportation Equipment |
| 3724 | Aircraft Engines and Engine Parts | Transportation Equipment |
| 3728 | Aircraft Parts and Auxiliary Equipment, Not Elsewhere Classified | Transportation Equipment |
| 3731 | Ship Building and Repairing | Transportation Equipment |
| 3732 | Boat Building and Repairing | Transportation Equipment |


## USL lightning.metastore.dd.dd_usl (3 sources joined by foreign keys)

### Company + industry (company -> sic_code FK)

```sql
SELECT c.entity_name, c.ticker, c.sic, s.description, s.division_desc FROM lightning.metastore.dd.dd_usl.company c JOIN lightning.metastore.dd.dd_usl.sic_code s ON s.sic_code = c.sic
```

| entity_name | ticker | sic | description | division_desc |
|---|---|---|---|---|
| Rivian Automotive, Inc. / DE | RIVN | 3711 | Motor Vehicles and Passenger Car Bodies | Manufacturing |
| Lucid Group, Inc. | LCID | 3711 | Motor Vehicles and Passenger Car Bodies | Manufacturing |

### Peer comparison: latest annual revenue, loss and cash burn

```sql
SELECT ticker, metric, period_end, value_usd FROM (
  SELECT c.ticker, f.metric, f.period_end, f.value_usd,
         ROW_NUMBER() OVER (PARTITION BY f.cik, f.metric ORDER BY f.period_end DESC, f.filed DESC) AS rn
  FROM lightning.metastore.dd.dd_usl.financial_fact f JOIN lightning.metastore.dd.dd_usl.company c ON c.cik = f.cik
  WHERE f.form = '10-K' AND (f.period_start IS NULL OR datediff(to_date(f.period_end), to_date(f.period_start)) > 300)
) t WHERE rn = 1 ORDER BY metric, ticker
```

| ticker | metric | period_end | value_usd |
|---|---|---|---|
| LCID | cash | 2025-12-31 | 9.97827E8 |
| RIVN | cash | 2025-12-31 | 3.579E9 |
| LCID | long_term_debt | 2025-12-31 | 2.046576E9 |
| RIVN | long_term_debt | 2025-12-31 | 4.44E9 |
| LCID | net_income | 2025-12-31 | -2.698051E9 |
| RIVN | net_income | 2025-12-31 | -3.646E9 |
| LCID | operating_cash_flow | 2025-12-31 | -2.931912E9 |
| RIVN | operating_cash_flow | 2025-12-31 | -7.79E8 |
| LCID | revenue | 2025-12-31 | 1.35379E9 |
| RIVN | revenue | 2025-12-31 | 5.387E9 |

### Cash runway proxy: cash / annual operating cash burn

```sql
SELECT ticker, period_end, ROUND(cash / -ocf, 2) AS years_of_runway FROM (
  SELECT c.ticker, f.period_end,
         MAX(CASE WHEN f.metric = 'cash' THEN f.value_usd END) AS cash,
         MAX(CASE WHEN f.metric = 'operating_cash_flow' AND datediff(to_date(f.period_end), to_date(f.period_start)) > 300
                  THEN f.value_usd END) AS ocf
  FROM lightning.metastore.dd.dd_usl.financial_fact f JOIN lightning.metastore.dd.dd_usl.company c ON c.cik = f.cik
  WHERE f.form = '10-K' GROUP BY c.ticker, f.period_end
) t WHERE ocf < 0 AND period_end >= '2023-01-01' ORDER BY ticker, period_end
```

| ticker | period_end | years_of_runway |
|---|---|---|
| LCID | 2023-12-31 | 0.55 |
| LCID | 2024-12-31 | 0.8 |
| LCID | 2025-12-31 | 0.34 |
| RIVN | 2023-12-31 | 1.61 |
| RIVN | 2024-12-31 | 3.09 |
| RIVN | 2025-12-31 | 4.59 |


## Data quality

### Rules on financial_fact

```sql
LIST DQ USL lightning.metastore.dd.dd_usl
```

| name | table | type | expression |
|---|---|---|---|
| sic_code | sic_code | Primary key constraints |  |
| cik | company | Primary key constraints |  |
| sic | company | Foreign key constraints |  |
| cik | financial_fact | Foreign key constraints |  |
| valid_reported_fact | financial_fact | Custom Data Quality | value_usd IS NOT NULL AND form IS NOT NULL AND (period_start IS NULL OR period_e |

### Run them

```sql
RUN DQ TABLE lightning.metastore.dd.dd_usl.financial_fact
```

| name | table | type | total_record | valid_record | invalid_record |
|---|---|---|---|---|---|
| valid_reported_fact | financial_fact | Custom Data Quality | 515 | 515 | 0 |
| cik | financial_fact | Foreign Key Constraint | 515 | 515 | 0 |
