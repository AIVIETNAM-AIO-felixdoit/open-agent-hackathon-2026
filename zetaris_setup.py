"""Onboard the due-diligence data into Zetaris: 3 REST sources, one USL, one DQ rule.

    python zetaris_setup.py --write        # only regenerate zetaris_sql/*.sql
    python zetaris_setup.py                # write and run everything (skips objects that already exist)
    python zetaris_setup.py --from 3       # resume from step 3

Sources (all public REST, Zetaris reads them live through CREATE LIGHTNING REST TABLE):
  SEC_DATA      SEC EDGAR XBRL company facts: revenue, net income, cash, operating cash flow, long-term debt
  SEC_PROFILES  SEC EDGAR submissions: legal name, ticker, SIC industry code, state of incorporation
  COMPANY_DNS   company_dns SIC hierarchy (division / major group / industry group)
USL lightning.metastore.dd.dd_usl joins them: sic_code <- company <- financial_fact (foreign keys).
"""
import argparse
import os
import sys
import time
from pathlib import Path

from zetaris_client import USL, Zetaris, ZetarisError

sys.stdout.reconfigure(encoding="utf-8")

COMPANIES = {"rivian": ("0001874178", "Rivian Automotive"), "lucid": ("0001811210", "Lucid Group")}
METRICS = {  # short name -> us-gaap XBRL concept
    "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "net_income": "NetIncomeLoss",
    "cash": "CashAndCashEquivalentsAtCarryingValue",
    "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
    "long_term_debt": "LongTermDebtNoncurrent",
}
INSTANT = {"cash", "long_term_debt"}  # balance-sheet values: XBRL gives no period start
UA_PLACEHOLDER = "YOUR_APP_NAME YOUR_CONTACT_EMAIL"
SQL_DIR = Path(__file__).parent / "zetaris_sql"


def rest_table(name, database, endpoint):
    return f"""CREATE LIGHTNING REST TABLE {name} FROM {database} REQUEST(
    endpoint "{endpoint}",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    user-agent "{UA_PLACEHOLDER}"
) BODY ()"""


def sources() -> list[str]:
    s = ['CREATE LIGHTNING DATABASE SEC_DATA DESCRIBE BY "SEC EDGAR XBRL financial facts for due diligence"',
         'CREATE LIGHTNING DATABASE SEC_PROFILES DESCRIBE BY "SEC EDGAR company profiles from the submissions endpoint"',
         'CREATE LIGHTNING DATABASE COMPANY_DNS DESCRIBE BY "company_dns SIC industry hierarchy"']
    for co, (cik, _) in COMPANIES.items():
        for metric, concept in METRICS.items():
            s.append(rest_table(f"{co}_{metric}_raw", "SEC_DATA",
                                f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"))
            s.append(f"CACHE TABLE sec_data.{co}_{metric}_raw")
        s.append(rest_table(f"{co}_submissions_raw", "SEC_PROFILES", f"https://data.sec.gov/submissions/CIK{cik}.json"))
        s.append(f"CACHE TABLE sec_profiles.{co}_submissions_raw")
    s.append(rest_table("sic_codes_raw", "COMPANY_DNS", "https://company-dns.mediumroast.io/V3.0/na/sic/code/%25"))
    s.append("CACHE TABLE company_dns.sic_codes_raw")
    return s


def usl() -> list[str]:
    facts = "\n    UNION ALL\n".join(
        f"""    SELECT '{cik}' AS cik, '{metric}' AS metric, tag AS concept, CAST(f.fy AS INT) AS fiscal_year,
           f.fp AS fiscal_period, {'CAST(NULL AS STRING)' if metric in INSTANT else 'f.`start`'} AS period_start, COALESCE(f.`end`, '') AS period_end,
           CAST(f.val AS DOUBLE) AS value_usd, f.form AS form, f.filed AS filed, f.accn AS accession
    FROM sec_data.{co}_{metric}_raw LATERAL VIEW explode(units.USD) AS f"""
        for co, (cik, _) in COMPANIES.items() for metric in METRICS)
    profiles = "\n    UNION ALL\n".join(
        f"""    SELECT COALESCE(cik, '') AS cik, COALESCE(name, '') AS entity_name, tickers[0] AS ticker, COALESCE(sic, '') AS sic, `sicDescription` AS sic_description_edgar,
           `stateOfIncorporation` AS state_of_incorporation, `fiscalYearEnd` AS fiscal_year_end
    FROM sec_profiles.{co}_submissions_raw""" for co in COMPANIES)
    return [
        "CREATE NAMESPACE IF NOT EXISTS lightning.metastore",
        "CREATE NAMESPACE IF NOT EXISTS lightning.metastore.dd",
        f"""COMPILE USL IF NOT EXISTS dd_usl DEPLOY NAMESPACE lightning.metastore.dd DDL
CREATE TABLE sic_code (
    sic_code varchar(4) NOT NULL PRIMARY KEY,
    description varchar(200),
    division_desc varchar(200),
    major_group_desc varchar(200),
    industry_group_desc varchar(200)
);

CREATE TABLE company (
    cik varchar(10) NOT NULL PRIMARY KEY,
    entity_name varchar(200) NOT NULL,
    ticker varchar(10),
    sic varchar(4) NOT NULL FOREIGN KEY REFERENCES sic_code(sic_code),
    sic_description_edgar varchar(200),
    state_of_incorporation varchar(10),
    fiscal_year_end varchar(4)
);

CREATE TABLE financial_fact (
    cik varchar(10) NOT NULL FOREIGN KEY REFERENCES company(cik),
    metric varchar(40) NOT NULL,
    concept varchar(120),
    fiscal_year int,
    fiscal_period varchar(4),
    period_start varchar(10),
    period_end varchar(10) NOT NULL,
    value_usd double,
    form varchar(12),
    filed varchar(10),
    accession varchar(25)
)""",
        f"UPDATE USL {USL} SET DESCRIPTION 'Due-diligence semantic layer: SEC EDGAR financial facts and company "
        "profiles joined to the SIC industry hierarchy. One model the agent queries instead of three raw APIs.'",
        f"""ACTIVATE USL TABLE {USL}.sic_code AS
SELECT sic_code, sic_val.description AS description, sic_val.division_desc AS division_desc,
       sic_val.major_group_desc AS major_group_desc, sic_val.industry_group_desc AS industry_group_desc
FROM company_dns.sic_codes_raw
LATERAL VIEW explode(
    from_json(to_json(data.sics), 'map<string, struct<description:string, division:string, division_desc:string, major_group:string, major_group_desc:string, industry_group:string, industry_group_desc:string>>')
) AS sic_code, sic_val""",
        f"""ACTIVATE USL TABLE {USL}.company AS
SELECT * FROM (
{profiles}
) AS companies""",
        f"""ACTIVATE USL TABLE {USL}.financial_fact AS
SELECT * FROM (
{facts}
) AS facts""",
        # DQ rule: a usable fact has a value, a known form, and a period that ends after it starts.
        f"""REGISTER DQ valid_reported_fact TABLE {USL}.financial_fact AS
value_usd IS NOT NULL AND form IS NOT NULL AND (period_start IS NULL OR period_end >= period_start)""",
    ]


def write_sql(steps):
    SQL_DIR.mkdir(exist_ok=True)
    for name, stmts in steps:
        body = "\n;\n\n".join(stmts) + "\n;\n"
        (SQL_DIR / f"{name}.sql").write_text(f"-- Generated by zetaris_setup.py. Statements are separated by ';' lines.\n\n{body}",
                                             encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="only write zetaris_sql/*.sql")
    parser.add_argument("--from", dest="start", type=int, default=1, help="first statement number to run")
    args = parser.parse_args()
    steps = [("01_sources_create", sources()), ("02_usl_create", usl())]
    write_sql(steps)
    if args.write:
        return
    ua = os.getenv("ZETARIS_USER_AGENT", "")
    if "@" not in ua:
        sys.exit("Set ZETARIS_USER_AGENT='app_name you@example.com' in .env (SEC EDGAR requires a contact)")
    z, n = Zetaris(), 0
    for _, stmts in steps:
        for sql in stmts:
            n += 1
            if n < args.start:
                continue
            first = sql.splitlines()[0][:90]
            started = time.time()
            try:
                z.query(sql.replace(UA_PLACEHOLDER, ua))
                print(f"[{n:2}] ok   {time.time() - started:5.1f}s  {first}")
            except ZetarisError as exc:
                if "exist" in str(exc).lower():
                    print(f"[{n:2}] skip (exists)  {first}")
                else:
                    print(f"[{n:2}] FAIL {first}\n      {exc}")
                    sys.exit(f"Fix and resume with: python zetaris_setup.py --from {n}")
            if "data.sec.gov" in sql:
                time.sleep(0.3)  # SEC fair-access limit is 10 requests/second


if __name__ == "__main__":
    main()
