# Container deployment and MySQL migration

## Start a new environment

1. Copy `.env.example` to `.env` and replace the database and JWT secrets.
2. Start the three services:

   ```bash
   docker compose up --build
   ```

3. Open `http://localhost:5173`. The API is also available at
   `http://localhost:8000/api/v1`.

MySQL creates the schema and knowledge spaces on the first start. The backend
entrypoint then runs the idempotent `scripts/init_db.py` to create the demo
users using the passwords from `.env`. The backend builds the connection URL
with SQLAlchemy's structured URL builder, so passwords containing URL-reserved
characters do not need manual encoding in Compose.

The host directories `data/`, `chroma_db/`, and `logs/` are mounted into the
backend container. MySQL data is stored in the `mysql_data` named volume.

## Move an existing SQLite database

Start MySQL and the backend once, then copy the legacy rows into the new MySQL
database. `--replace-seed` is required because a new deployment already
contains the four knowledge spaces and demo users:

```bash
docker compose exec backend python infra/migrations/migrate_sqlite_to_mysql.py \
  --sqlite /app/data/knowbase.db \
  --replace-seed
```

The migration preserves primary keys, JSON fields, audit history, feedback,
document versions, and relative document paths. It refuses to write into a
non-empty target unless `--replace-seed` is supplied.

The SQL scripts in `infra/mysql/init/` only run when MySQL initializes an empty
data directory. To re-run a migration from scratch, use a new database or
explicitly remove the `mysql_data` volume after backing it up.
