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

   You can also list the tables to confirm the schema was created:

   ```bash
   docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
   ```

> Note: `sql/init.sql` only runs the first time the database is created. If you change it,
> reset the database with `docker compose down -v` (this **deletes all data**) and start again.
