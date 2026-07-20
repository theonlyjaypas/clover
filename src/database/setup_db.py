import json
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "menu.db"))
JSON_PATH = os.path.join(os.path.dirname(__file__), "menu.json")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS menu_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id   INTEGER NOT NULL,
            name          TEXT NOT NULL,
            price         TEXT NOT NULL,
            price_numeric REAL,
            description   TEXT,
            FOREIGN KEY (category_id) REFERENCES categories (id)
        );
    """)


def parse_price(price_str: str) -> float | None:
    try:
        return float(price_str.replace("$", "").strip())
    except ValueError:
        return None


def load_json_to_db(conn: sqlite3.Connection, data: dict) -> None:
    for section in data["menu"]:
        category = section["category"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,)
        )
        conn.execute("SELECT id FROM categories WHERE name = ?", (category,))
        cat_id = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (category,)
        ).fetchone()[0]

        for item in section["items"]:
            conn.execute(
                """
                INSERT INTO menu_items (category_id, name, price, price_numeric, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cat_id,
                    item["name"],
                    item["price"],
                    parse_price(item["price"]),
                    item.get("description"),
                ),
            )


def main() -> None:
    with open(JSON_PATH) as f:
        data = json.load(f)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        create_schema(conn)
        load_json_to_db(conn, data)
        conn.commit()

        item_count = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
        cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        print(f"Created {DB_PATH}")
        print(f"  {cat_count} categories, {item_count} menu items")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
