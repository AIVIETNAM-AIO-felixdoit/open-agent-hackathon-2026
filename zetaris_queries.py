"""Run the proof queries for each Zetaris source, the USL and the DQ rule; save results to zetaris_sql/results.md.

    python zetaris_queries.py
"""
import sys
import time
from pathlib import Path

from zetaris_client import USL, Zetaris

sys.stdout.reconfigure(encoding="utf-8")

QUERIES = [
    ("Source 1: SEC_DATA (EDGAR XBRL facts)", [
        ("Rivian annual revenue straight from the raw REST table",
         "SELECT f.`end` AS period_end, f.val AS revenue_usd, f.form, f.filed FROM sec_data.rivian_revenue_raw "
         "LATERAL VIEW explode(units.USD) AS f WHERE f.form = '10-K' AND f.fp = 'FY' ORDER BY f.filed DESC LIMIT 6"),
        ("Rivian cash on hand, every filing",
         "SELECT f.`end` AS period_end, f.val AS cash_usd, f.form FROM sec_data.rivian_cash_raw "
         "LATERAL VIEW explode(units.USD) AS f ORDER BY f.`end` DESC LIMIT 8"),
        ("Lucid net income by fiscal year",
         "SELECT f.fy, f.`end` AS period_end, f.val AS net_income_usd FROM sec_data.lucid_net_income_raw "
         "LATERAL VIEW explode(units.USD) AS f WHERE f.form = '10-K' AND f.fp = 'FY' ORDER BY f.filed DESC LIMIT 6"),
    ]),
    ("Source 2: SEC_PROFILES (EDGAR submissions)", [
        ("Company profile fields",
         "SELECT name, cik, sic, `sicDescription`, `stateOfIncorporation`, `fiscalYearEnd`, tickers[0] AS ticker "
         "FROM sec_profiles.rivian_submissions_raw"),
        ("Most recent Rivian filings (form types)",
         "SELECT form, filed FROM (SELECT posexplode(filings.recent.form) AS (i, form), filings.recent.filingDate AS dates "
         "FROM sec_profiles.rivian_submissions_raw) x LATERAL VIEW explode(array(dates[i])) AS filed ORDER BY filed DESC LIMIT 8"),
    ]),
    ("Source 3: COMPANY_DNS (SIC hierarchy)", [
        ("SIC codes loaded",
         "SELECT COUNT(*) AS sic_codes FROM company_dns.sic_codes_raw LATERAL VIEW explode(from_json(to_json(data.sics), "
         "'map<string, struct<description:string>>')) AS code, v"),
        ("Transportation-equipment codes around the EV industry",
         "SELECT sic_code, description, major_group_desc FROM " + USL + ".sic_code "
         "WHERE sic_code LIKE '37%' ORDER BY sic_code LIMIT 10"),
    ]),
    ("USL lightning.metastore.dd.dd_usl (3 sources joined by foreign keys)", [
        ("Company + industry (company -> sic_code FK)",
         f"SELECT c.entity_name, c.ticker, c.sic, s.description, s.division_desc FROM {USL}.company c "
         f"JOIN {USL}.sic_code s ON s.sic_code = c.sic"),
        ("Peer comparison: latest annual revenue, loss and cash burn",
         f"""SELECT ticker, metric, period_end, value_usd FROM (
  SELECT c.ticker, f.metric, f.period_end, f.value_usd,
         ROW_NUMBER() OVER (PARTITION BY f.cik, f.metric ORDER BY f.period_end DESC, f.filed DESC) AS rn
  FROM {USL}.financial_fact f JOIN {USL}.company c ON c.cik = f.cik
  WHERE f.form = '10-K' AND (f.period_start IS NULL OR datediff(to_date(f.period_end), to_date(f.period_start)) > 300)
) t WHERE rn = 1 ORDER BY metric, ticker"""),
        ("Cash runway proxy: cash / annual operating cash burn",
         f"""SELECT ticker, period_end, ROUND(cash / -ocf, 2) AS years_of_runway FROM (
  SELECT c.ticker, f.period_end,
         MAX(CASE WHEN f.metric = 'cash' THEN f.value_usd END) AS cash,
         MAX(CASE WHEN f.metric = 'operating_cash_flow' AND datediff(to_date(f.period_end), to_date(f.period_start)) > 300
                  THEN f.value_usd END) AS ocf
  FROM {USL}.financial_fact f JOIN {USL}.company c ON c.cik = f.cik
  WHERE f.form = '10-K' GROUP BY c.ticker, f.period_end
) t WHERE ocf < 0 AND period_end >= '2023-01-01' ORDER BY ticker, period_end"""),
    ]),
    ("Data quality", [
        ("Rules on financial_fact", f"LIST DQ USL {USL}"),
        ("Run them", f"RUN DQ TABLE {USL}.financial_fact"),
    ]),
]


def table(rows):
    if not rows:
        return "_no rows_\n"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]).replace("|", "/")[:80] for c in cols) + " |" for r in rows[:12]]
    return "\n".join(out) + "\n"


def main():
    z = Zetaris()
    md = [f"# Zetaris proof queries\n\nRun with `python zetaris_queries.py` on {time.strftime('%Y-%m-%d %H:%M')} "
          "against a local Zetaris platform. Every query below ran live; results are pasted as returned.\n"]
    for section, queries in QUERIES:
        md.append(f"\n## {section}\n")
        for title, sql in queries:
            started = time.time()
            try:
                rows = z.query(sql)
                result = table(rows)
                print(f"ok   {time.time() - started:5.1f}s {len(rows):4} rows  {title}")
            except Exception as exc:
                result = f"FAILED: `{str(exc)[:300]}`\n"
                print(f"FAIL {title}: {exc}")
            md.append(f"### {title}\n\n```sql\n{sql}\n```\n\n{result}")
    (Path(__file__).parent / "zetaris_sql" / "results.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
