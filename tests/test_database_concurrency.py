from concurrent.futures import ThreadPoolExecutor
from threading import Event

from kenkui_server.storage.database import Database


def test_reader_cannot_observe_an_uncommitted_shared_connection_write(tmp_path):
    database = Database(tmp_path / "concurrency.sqlite3")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE counter (value INTEGER)")
        connection.execute("INSERT INTO counter VALUES (0)")
    started = Event()

    def read():
        started.set()
        return database.query("SELECT value FROM counter").fetchone()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.transaction() as connection:
            connection.execute("UPDATE counter SET value = 1")
            future = executor.submit(read)
            assert started.wait(timeout=5)
            assert not future.done()
            connection.execute("UPDATE counter SET value = 2")
        assert future.result(timeout=5) == (2,)
    database.close()
