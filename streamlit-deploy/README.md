# MSSP ACO Benchmark

A web app for benchmarking Medicare Shared Savings Program ACOs against
their peer cohort, powered by the CMS Performance Year Financial and
Quality Results Public-Use File.

Public CMS data only. No proprietary, claims-level, or PHI data.

## Three modes

1. **Look up an ACO** — Search by name. See where the ACO ranks across
   savings rate, quality score, per-capita expenditure, admits/1000, ED
   visits/1000, readmits, SNF utilization, risk score, dual %, and more.
   Each metric ranks against four overlapping cohorts: same track, same
   revenue category, same size band, and all ACOs.

2. **Benchmark my numbers** — Type in metrics and pick the peer cohort.
   Useful for forecasted scenarios or evaluating the tool privately.

3. **Ask the data** — Free-form Q&A against the pre-computed cohort
   statistics.

## Run locally

```bash
python3 -m pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repo (public or private — both work).
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in.
3. Click **New app**. Point it at your repo, branch, and `app.py`.
4. Open the app's settings → **Secrets**, paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "your-shared-passphrase"  # optional gate
   ```
5. Deploy. The first build takes ~2 minutes.

If `APP_PASSWORD` is set, the app prompts visitors for a passphrase
before showing any content.

## Refresh the data

CMS publishes the next year's PUF in late summer of the following year.
To refresh:

```bash
cd data
# Replace the URL with the latest CMS-hosted file:
curl -O <new-csv-url>
cd ..
python3 build_data.py
```

Then commit the regenerated `data/*.json` and `data/*.sqlite` files.

## Source

[CMS Performance Year Financial and Quality Results](
https://data.cms.gov/medicare-shared-savings-program/performance-year-financial-and-quality-results
)
