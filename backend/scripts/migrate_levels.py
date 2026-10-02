"""Remove legacy Firebase boards while preserving player identities and attempts."""

from backend.app.catalog import load_levels
from backend.app.config import Settings
from backend.app.repository import build_repository


def main():
    settings = Settings()
    if settings.storage_backend != "firestore":
        raise SystemExit("This migration requires Firestore storage.")
    levels = load_levels(settings.levels_dir)
    store = build_repository(settings)
    store.set_catalog(levels)
    try:
        print(store.migrate_git_catalog())
    finally:
        store.close()


if __name__ == "__main__":
    main()
