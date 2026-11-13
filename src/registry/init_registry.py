from src.config import settings
from src.registry.db import ToolRegistryDB
from src.registry.seed_data import seed_tools

def main():
    print(f"Initializing Tool Registry at: {settings.DATABASE_PATH}")
    db = ToolRegistryDB(settings.DATABASE_PATH)
    count = seed_tools(db)
    print(f"Successfully registered {count} PayPal API tools into SQLite.")

if __name__ == "__main__":
    main()
