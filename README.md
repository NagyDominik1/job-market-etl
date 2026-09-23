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

> Note: `sql/init.sql` only runs the first time the database is created. If you change it,
> reset the database with `docker compose down -v` (this **deletes all data**) and start again.
