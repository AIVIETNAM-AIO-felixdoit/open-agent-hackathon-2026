# How the USL helps in our work

**Our users** are procurement, credit and M&A analysts who must decide whether to sign a multi-year contract with, lend to, or invest in a company. Their question is never "what does the web say?"; it is "can I defend this decision to a risk committee?".

## The problem the USL solves for them

Web search gives the agent news, lawsuits and opinions. It does not give reliable numbers: articles quote revenue rounded, from different fiscal years, sometimes wrong. A due-diligence memo built only on web pages fails the first question a risk committee asks: *where did this number come from?*

The USL gives the agent **one governed table of audited numbers** with provenance on every row:

| Column | Why the analyst cares |
|---|---|
| `metric`, `fiscal_year`, `period_end`, `value_usd` | the number itself, on a consistent fiscal basis |
| `form`, `filed`, `accession` | exactly which SEC filing it came from, so a human can open it |
| FK to `company` → FK to `sic_code` | the industry the SEC classifies the company in, so the peer comparison is like for like |

## What changes in the agent's output

1. **Financial claims cite filings, not blogs.** In the recorded Rivian runs the memo's financial reasons and the cash-runway risk cite the Zetaris source (SEC 10-K figures), and the Critic never flagged a financial claim as unsupported; its rejections were all about non-financial areas the web evidence did not cover.
2. **Peer context comes for free.** Because Rivian and Lucid share SIC 3711 in the USL, the agent can say "Rivian's cash covers about 4.6 years of its 2025 operating cash burn; Lucid's covers about 0.3 years", a comparison an analyst would otherwise build in a spreadsheet.
3. **Contradictions surface.** When a web article quotes an older or rounded figure, the Analyst can flag the disagreement with the filing value instead of silently picking one.
4. **Data quality is visible.** The DQ result (`valid_reported_fact 515/515`) is part of the evidence text and shows in the UI trace, so the reviewer sees that the numbers passed checks before reading the conclusion.

## Who else in a firm would use the same model

The same `dd_usl` serves the credit team (runway, debt), the supplier-risk team (revenue trend, cash burn) and the BI dashboard, all reading one definition of "revenue" instead of three copies of parsing code.
