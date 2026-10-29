#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
    DATABASE_URL=$(python - <<'PY'
import os
from urllib.parse import quote_plus

user = quote_plus(os.environ["MYSQL_USER"])
password = quote_plus(os.environ["MYSQL_PASSWORD"])
host = os.environ.get("MYSQL_HOST", "mysql")
port = os.environ.get("MYSQL_PORT", "3306")
database = quote_plus(os.environ["MYSQL_DATABASE"])
print(f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4")
PY
    )
    export DATABASE_URL
fi

python scripts/init_db.py

exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
