import json
import asyncio
import aiosqlite
import os

JSON_DB_PATH = os.path.join('app', 'maigret', 'resources', 'data.json')
SQLITE_DB_PATH = os.path.join('app', 'maigret', 'resources', 'maigret.db')

async def create_schema(db):
    await db.execute("""
    CREATE TABLE IF NOT EXISTS engines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        data TEXT
    );
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        username_claimed TEXT,
        username_unclaimed TEXT,
        url_main TEXT,
        url TEXT NOT NULL,
        disabled BOOLEAN DEFAULT 0,
        similar_search BOOLEAN DEFAULT 0,
        ignore_403 BOOLEAN DEFAULT 0,
        type TEXT DEFAULT 'username',
        headers TEXT,
        errors TEXT,
        activation TEXT,
        regex_check TEXT,
        url_probe TEXT,
        check_type TEXT,
        request_head_only BOOLEAN DEFAULT 0,
        get_params TEXT,
        presense_strs TEXT,
        absence_strs TEXT,
        engine_id INTEGER,
        engine_data TEXT,
        alexa_rank INTEGER,
        source TEXT,
        protocol TEXT,
        FOREIGN KEY (engine_id) REFERENCES engines (id)
    );
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    );
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS site_tags (
        site_id INTEGER,
        tag_id INTEGER,
        PRIMARY KEY (site_id, tag_id),
        FOREIGN KEY (site_id) REFERENCES sites (id),
        FOREIGN KEY (tag_id) REFERENCES tags (id)
    );
    """)
    await db.commit()

async def migrate_data():
    if os.path.exists(SQLITE_DB_PATH):
        os.remove(SQLITE_DB_PATH)
        print(f"Removed existing database at {SQLITE_DB_PATH} to start fresh.")

    try:
        with open(JSON_DB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {JSON_DB_PATH} not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {JSON_DB_PATH}.")
        return

    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        await create_schema(db)

        engine_map = {}
        if 'engines' in data:
            for name, engine_data in data['engines'].items():
                cursor = await db.execute("INSERT INTO engines (name, data) VALUES (?, ?)", (name, json.dumps(engine_data)))
                engine_map[name] = cursor.lastrowid

        tag_map = {}
        if 'tags' in data:
            for tag_name in data['tags']:
                cursor = await db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                if cursor.lastrowid:
                    tag_map[tag_name] = cursor.lastrowid
                else:
                    cursor = await db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
                    row = await cursor.fetchone()
                    tag_map[tag_name] = row[0]

        if 'sites' in data:
            for name, site_data in data['sites'].items():
                print(f"Processing site: {name}")
                if not site_data.get('url'):
                    print(f"  [ERROR] Site '{name}' has no URL. Skipping.")
                    continue

                engine_id = engine_map.get(site_data.get('engine'))

                db_site_data = {
                    'name': name,
                    'username_claimed': site_data.get('usernameClaimed'),
                    'username_unclaimed': site_data.get('usernameUnclaimed'),
                    'url_main': site_data.get('urlMain'),
                    'url': site_data.get('url'),
                    'disabled': site_data.get('disabled', False),
                    'similar_search': site_data.get('similarSearch', False),
                    'ignore_403': site_data.get('ignore403', False),
                    'type': site_data.get('type', 'username'),
                    'headers': json.dumps(site_data.get('headers', {})),
                    'errors': json.dumps(site_data.get('errors', {})),
                    'activation': json.dumps(site_data.get('activation', {})),
                    'regex_check': site_data.get('regexCheck'),
                    'url_probe': site_data.get('urlProbe'),
                    'check_type': site_data.get('checkType'),
                    'request_head_only': site_data.get('requestHeadOnly', False),
                    'get_params': json.dumps(site_data.get('getParams', {})),
                    'presense_strs': json.dumps(site_data.get('presenseStrs', [])),
                    'absence_strs': json.dumps(site_data.get('absenceStrs', [])),
                    'engine_id': engine_id,
                    'engine_data': json.dumps(site_data.get('engineData', {})),
                    'alexa_rank': site_data.get('alexaRank'),
                    'source': site_data.get('source'),
                    'protocol': site_data.get('protocol'),
                }

                columns = ', '.join(db_site_data.keys())
                placeholders = ', '.join('?' for _ in db_site_data)
                sql = f"INSERT INTO sites ({columns}) VALUES ({placeholders})"

                cursor = await db.execute(sql, tuple(db_site_data.values()))
                site_id = cursor.lastrowid

                site_tags = site_data.get('tags', [])
                for tag_name in site_tags:
                    if tag_name not in tag_map:
                        cursor = await db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                        if cursor.lastrowid:
                           tag_map[tag_name] = cursor.lastrowid
                        else:
                            cursor = await db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
                            row = await cursor.fetchone()
                            tag_map[tag_name] = row[0]

                    tag_id = tag_map[tag_name]
                    await db.execute("INSERT INTO site_tags (site_id, tag_id) VALUES (?, ?)", (site_id, tag_id))

        await db.commit()
        print("Database migration completed successfully.")

if __name__ == "__main__":
    asyncio.run(migrate_data())