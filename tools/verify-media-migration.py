#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare the actual v1->v2->v3 migrations with exported Room schemas using host SQLite."""
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/media-android"
SCHEMAS = BASE / "schemas/io.github.joyelliot.zivplayer.data.media.ZivMediaDatabase"
SOURCE = BASE / "src/main/kotlin/io/github/joyelliot/zivplayer/data/media/MediaRepositories.kt"


def execute_schema(connection, schema):
    for entity in schema["database"]["entities"]:
        connection.execute(entity["createSql"].replace("${TABLE_NAME}", entity["tableName"]))
        for index in entity.get("indices", []):
            connection.execute(index["createSql"].replace("${TABLE_NAME}", entity["tableName"]))


def structure(connection):
    tables = sorted(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    return {table: {
        "columns": list(connection.execute(f"PRAGMA table_info('{table}')")),
        "foreignKeys": list(connection.execute(f"PRAGMA foreign_key_list('{table}')")),
        "indexes": {row[1]: (row[2], list(connection.execute(f"PRAGMA index_info('{row[1]}')")))
                    for row in connection.execute(f"PRAGMA index_list('{table}')")},
    } for table in tables}


def rejects(connection, statement, parameters=()):
    try:
        connection.execute(statement, parameters)
    except sqlite3.IntegrityError:
        return
    raise AssertionError(f"Expected a constraint violation: {statement}")


def migration_sql(source, name):
    migration = source.split(f"val {name}:", 1)[1].split("internal val MIGRATION_", 1)[0]
    # Keep statement order and bound extraction to one migration declaration.
    return [multiline or single for multiline, single in
            re.findall(r'db\.execSQL\((?:"""(.*?)"""|"([^"\n]*)")\)', migration, re.S)]


def main():
    v1 = json.loads((SCHEMAS / "1.json").read_text(encoding="utf-8"))
    v2 = json.loads((SCHEMAS / "2.json").read_text(encoding="utf-8"))
    v3 = json.loads((SCHEMAS / "3.json").read_text(encoding="utf-8"))
    source = SOURCE.read_text(encoding="utf-8")
    # Read literal SQL from the implementation, rather than maintaining a second migration.
    sql = migration_sql(source, "MIGRATION_1_2")
    visibility_sql = migration_sql(source, "MIGRATION_2_3")
    assert len(sql) == 7, f"Unexpected migration shape: {len(sql)} statements"
    assert len(visibility_sql) == 1, f"Unexpected visibility migration shape: {len(visibility_sql)} statements"
    migrated = sqlite3.connect(":memory:")
    fresh = sqlite3.connect(":memory:")
    for connection in (migrated, fresh):
        connection.execute("PRAGMA foreign_keys=ON")
    execute_schema(migrated, v1)
    execute_schema(fresh, v2)
    columns = [row[1] for row in migrated.execute("PRAGMA table_info(recent_media)")]
    values = {"media_id": "sentinel", "source_uri": "content://test/sentinel", "title": "迁移验证",
              "mime_type": "video/mp4", "added_at_epoch_ms": 100, "last_opened_at_epoch_ms": 200,
              "checkpoint_position_ms": 1234, "checkpoint_duration_ms": 9000,
              "checkpoint_completed": 0, "checkpoint_updated_at_epoch_ms": 300}
    # Use exported field names to fail if the original history schema changes unexpectedly.
    required = [row[1] for row in migrated.execute("PRAGMA table_info(recent_media)") if row[3]]
    for column in required:
        assert column in values, f"Add sentinel value for required column: {column}"
    migrated.execute(f"INSERT INTO recent_media ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     [values.get(column) for column in columns])
    before = list(migrated.execute("SELECT * FROM recent_media"))
    for statement in sql:
        migrated.execute(statement)
    assert structure(migrated) == structure(fresh), "Migrated schema differs from a fresh v2 database"
    assert list(migrated.execute("SELECT * FROM recent_media")) == before, "History changed during migration"
    for statement in sql:
        migrated.execute(statement)
    # v2->v3 adds visibility without rebuilding history, identity or checkpoint columns.
    fresh_v3 = sqlite3.connect(":memory:")
    execute_schema(fresh_v3, v3)
    for statement in visibility_sql:
        migrated.execute(statement)
    assert structure(migrated) == structure(fresh_v3), "Migrated schema differs from a fresh v3 database"
    assert list(migrated.execute(f"SELECT {','.join(columns)} FROM recent_media")) == before, "History changed during v3 migration"
    assert list(migrated.execute("SELECT visible_in_history FROM recent_media")) == [(1,)], "Existing history became hidden"
    rejects(migrated, "INSERT INTO recent_media(media_id,source_uri,added_at_epoch_ms,last_opened_at_epoch_ms) VALUES ('duplicate','content://test/sentinel',0,0)")
    migrated.execute("INSERT INTO media_track_choices VALUES ('sentinel','AUDIO',0,'en',NULL,'aac',0,0)")
    migrated.execute("INSERT INTO media_external_subtitles VALUES ('sentinel','resource','Subtitle')")
    rejects(migrated, "INSERT INTO media_external_subtitles VALUES ('missing','resource','Subtitle')")
    migrated.execute("INSERT INTO library_folders VALUES ('folder','content://test/tree','Folder','READY',NULL,1,NULL,NULL)")
    migrated.execute("INSERT INTO library_media VALUES ('folder','content://test/child','Child','video/mp4','Child',1,NULL,1,0)")
    migrated.execute("DELETE FROM recent_media WHERE media_id='sentinel'")
    migrated.execute("DELETE FROM library_folders WHERE folder_id='folder'")
    for table in ("media_track_choices", "media_external_subtitles", "library_media"):
        assert migrated.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not list(migrated.execute("PRAGMA foreign_key_check"))
    assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print(json.dumps({"kind": "host-room-migration", "from": 1, "to": 3,
                      "statements": len(sql) + len(visibility_sql), "tables": len(structure(fresh_v3)),
                      "schemaParity": "passed", "historyPreserved": "passed", "constraintsAndCascades": "passed",
                      "deviceRoomOpen": "not run"}, indent=2))


if __name__ == "__main__":
    main()
