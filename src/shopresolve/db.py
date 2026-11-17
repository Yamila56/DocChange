from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def make_database(url: str):
    engine = create_async_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def set_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
            # SQLite has no SELECT FOR UPDATE. Acquire its writer reservation at
            # transaction start, before reading lease/event counters, so concurrent
            # short transactions cannot deadlock while upgrading a shared read lock.
            connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def begin_sqlite_transaction(connection):
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine, async_sessionmaker(engine, expire_on_commit=False)
