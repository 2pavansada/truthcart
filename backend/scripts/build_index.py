import sys
from pathlib import Path

# Add backend to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.settings import load_settings
from app.ingest.build_pipeline import build_all


def main():
    print("Building TruthCart database and index...")
    settings = load_settings()
    report = build_all(settings)
    print("Build completed successfully!")
    print(f"Products indexed: {report['counts']['products']}")
    print(f"Quarantined: {report['counts']['quarantined']}")
    print(f"Policy sections: {report['counts']['policy_sections']}")
    print(f"Total build time: {report['total_ms']} ms")


if __name__ == "__main__":
    main()
