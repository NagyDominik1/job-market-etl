# data/manual — postings I add by hand

Put `.csv` files here; the `csv` source reads every `*.csv` file in this folder on
each pipeline run. **Files here are not committed to git** (see `.gitignore`), only
this README. An example is in [`data/examples/example.csv`](../examples/example.csv).

## Format

First line is the header, with these six columns in any order:

```
title,company,city,description,url,deadline
```

| Column | Required | Notes |
|--------|----------|-------|
| `title` | yes | job title; rows without a title are skipped |
| `company` | yes | rows without a company can't be deduplicated and get a `transform_error` |
| `city` | no | e.g. `Novi Sad`, `Újvidék`, `Szabadka`, `Remote` (aliases are matched) |
| `description` | no | plain text; put it in "double quotes" if it contains commas |
| `url` | no | link to the posting; also used to recognise the same row next time |
| `deadline` | no | `2026-11-15`, `2026.11.15.` or `15.11.2026.` |

- Encoding: UTF-8 (Excel: *Save as → CSV UTF-8*). Both `,` and `;` separators work.
- Each row's ID is a hash of its `url`, or of `title + company + city` if there is no url.
  Editing other columns of a row (e.g. the deadline) updates the same posting; changing
  the url, title, company or city makes it a new one.
