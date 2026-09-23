# job-market-etl

A small ETL (Extract, Transform, Load) pipeline that collects job postings from the
[Arbeitnow Job Board API](https://www.arbeitnow.com/api/job-board-api), cleans them with
pandas, and stores them in a PostgreSQL database running in Docker. Every pipeline run is
logged to an `etl_runs` table so you can see when it ran, how many rows it processed,
and whether it succeeded.

## Getting started

Requirements: [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose).

1. Create your local config file from the template, then edit the values:

   ```bash
   cp .env.example .env
   ```

   (On Windows PowerShell: `Copy-Item .env.example .env`)

2. Start the database in the background:

   ```bash
   docker compose up -d
   ```

3. Check that it's healthy. The `STATUS` column should say `(healthy)`:

   ```bash
   docker compose ps
   ```

   You can also list the tables to confirm the schema was created
   (replace `etl_user` / `job_market` with the values from your `.env`):

   ```bash
   docker compose exec postgres psql -U etl_user -d job_market -c "\dt"
   ```

## Extract

The extract step downloads job postings from the Arbeitnow API and saves them, unchanged,
to `data/raw/jobs_<UTC timestamp>.json`.

Set up Python once (Windows commands shown; on macOS/Linux use `.venv/bin/python`):

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Run it:

```bash
.venv\Scripts\python -m src.extract
```

Optional environment variables:

| Variable    | Default                                        | Meaning                        |
|-------------|------------------------------------------------|--------------------------------|
| `API_URL`   | `https://www.arbeitnow.com/api/job-board-api`  | First page to download         |
| `MAX_PAGES` | `3`                                            | Maximum number of pages to get |

Temporary failures (connection errors, timeouts, HTTP 429 and 5xx) are retried up to 3 times
with exponential backoff. If any page still fails, no file is written and the script exits
with code 1.

## Transform

The transform step (`src/transform.py`) cleans the newest raw file with pandas into a table
whose columns match the `jobs` database table. It does not write anything yet.

```bash
.venv\Scripts\python -m src.transform                  # newest file in data/raw/
.venv\Scripts\python -m src.transform path\to\file.json  # a specific file
```

What is cleaned, and why:

- **Descriptions**: HTML is converted to plain text (one paragraph or list item per line),
  and entities like `&amp;` become characters. Some API descriptions are HTML-escaped twice
  (`&lt;p&gt;`); these are cleaned too. Plain text is easier to search and analyse.
- **Titles**: gender markers such as `(m/w/d)` or `(f/m/d)` are removed, so that
  "Developer (m/w/d)" and "Developer" count as the same job title.
- **Text fields**: surrounding spaces are removed and empty strings become `NULL`,
  so "missing" always looks the same.
- **`tags` / `job_types`**: missing values become an empty list, so they are always lists.
- **Dates**: `created_at` (Unix seconds) becomes a UTC datetime called `posted_at`.
- **Bad rows**: rows without a `slug` or `title` are dropped (the table requires them),
  and duplicate slugs are dropped (the slug is the primary key). The counts are logged.

Run the tests:

```bash
.venv\Scripts\python -m pytest
```

## Load

The load step (`src/load.py`) writes the cleaned jobs into the `jobs` table with an
**upsert** (`INSERT ... ON CONFLICT (slug) DO UPDATE`):

- a job with a new `slug` is inserted; `first_seen_at` and `last_seen_at` are set to now
- a job that already exists is updated with the latest data and `last_seen_at = now()`;
  `first_seen_at` is kept, so you can see how long a job has been online
- all rows are written in **one transaction**: if anything fails, everything is rolled back,
  so the table is never half-loaded

Database settings are read from `.env` (`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`) by `src/db.py`. Variables already set in your shell
take priority over `.env`.

## Run the full pipeline

Make sure the database is running (`docker compose up -d`), then:

```bash
.venv\Scripts\python -m src.main
```

This runs extract → transform → load and records every run in the `etl_runs` table:
`running` at the start, then `success` (with row counts) or `failed` (with the error message).
On failure the script exits with code 1. Running it again is safe: existing jobs are updated,
not duplicated.

Check the results:

```bash
docker compose exec postgres psql -U etl_user -d job_market -c "SELECT count(*) FROM jobs;"
docker compose exec postgres psql -U etl_user -d job_market -c "SELECT id, status, rows_extracted, rows_loaded, error_message FROM etl_runs ORDER BY id;"
```

> Note: `sql/init.sql` only runs the first time the database is created. If you change it,
> reset the database with `docker compose down -v` (this **deletes all data**) and start again.
