"""Copy the legacy KnowBase SQLite rows into an initialized MySQL 8 database."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.database import get_database_url


TABLES = (
    "users",
    "knowledge_spaces",
    "documents",
    "document_versions",
    "chat_sessions",
    "chat_messages",
    "feedbacks",
    "audit_logs",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate an existing KnowBase SQLite database to MySQL 8."
    )
    parser.add_argument(
        "--sqlite",
        default="data/knowbase.db",
        help="Path to the source SQLite database (default: data/knowbase.db).",
    )
    parser.add_argument(
        "--mysql-url",
        default=None,
        help="SQLAlchemy MySQL URL; defaults to the application's database configuration.",
    )
    parser.add_argument(
        "--replace-seed",
        action="store_true",
        help="Delete target application rows before copying. Intended for a new deployment only.",
    )
    return parser.parse_args()


def sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info(`{table}`)")]


def mysql_columns(connection, table: str) -> set[str]:
    return {row[0] for row in connection.exec_driver_sql(f"SHOW COLUMNS FROM `{table}`")}


def target_row_count(connection) -> int:
    return sum(
        connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        for table in TABLES
    )


def migrate(sqlite_path: Path, mysql_url: str | URL, replace_seed: bool) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    driver_name = mysql_url.drivername if isinstance(mysql_url, URL) else mysql_url
    if not mysql_url or not driver_name.startswith("mysql+pymysql"):
        raise ValueError("The target database must use the mysql+pymysql driver")

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    engine = create_engine(mysql_url, pool_pre_ping=True, future=True)
    migrated: dict[str, int] = {}

    try:
        with engine.connect() as target:
            existing_rows = target_row_count(target)
            target.commit()
            if existing_rows and not replace_seed:
                raise RuntimeError(
                    f"Target contains {existing_rows} application rows. "
                    "Use a fresh database or pass --replace-seed explicitly."
                )

            target.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            target.commit()
            try:
                with target.begin():
                    if replace_seed:
                        for table in reversed(TABLES):
                            target.execute(text(f"DELETE FROM `{table}`"))

                    for table in TABLES:
                        source_names = sqlite_columns(source, table)
                        if not source_names:
                            migrated[table] = 0
                            continue

                        target_names = mysql_columns(target, table)
                        columns = [name for name in source_names if name in target_names]
                        rows = [dict(row) for row in source.execute(f"SELECT * FROM `{table}`")]
                        if not rows:
                            migrated[table] = 0
                            continue

                        column_sql = ", ".join(f"`{name}`" for name in columns)
                        value_sql = ", ".join(f":{name}" for name in columns)
                        statement = text(
                            f"INSERT INTO `{table}` ({column_sql}) VALUES ({value_sql})"
                        )
                        payload = [
                            {name: row.get(name) for name in columns}
                            for row in rows
                        ]
                        target.execute(statement, payload)
                        migrated[table] = len(payload)
            finally:
                target.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
                target.commit()
    finally:
        source.close()
        engine.dispose()

    return migrated


def main() -> None:
    args = parse_args()
    mysql_url = args.mysql_url or get_database_url()
    counts = migrate(Path(args.sqlite).resolve(), mysql_url, args.replace_seed)
    print("SQLite -> MySQL migration completed:")
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
