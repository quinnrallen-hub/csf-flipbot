#!/usr/bin/env python3
"""
Download the Steam Market CS:GO price history dataset from Kaggle.
Requires: pip install kaggle
Setup:  place your kaggle.json at ~/.kaggle/kaggle.json (get it from kaggle.com/settings)
"""

import os
import sys
import zipfile
import subprocess
from pathlib import Path

DATASET = "leawind/steam-market-price-dataset-csgo"
DATA_DIR = Path(__file__).parent / "data"


def download():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    zip_path = DATA_DIR / "steam-market-price-dataset-csgo.zip"

    if (DATA_DIR / "dataset_publish" / "item_index.csv").exists():
        print("Data already downloaded.")
        return

    # Check kaggle CLI
    try:
        subprocess.run(["kaggle", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("kaggle CLI not found. Install it:")
        print("  pip install kaggle")
        print("Then add your API key:")
        print("  mkdir -p ~/.kaggle && cp kaggle.json ~/.kaggle/ && chmod 600 ~/.kaggle/kaggle.json")
        sys.exit(1)

    print(f"Downloading {DATASET} ...")
    subprocess.run(
        ["kaggle", "datasets", "download", "-d", DATASET, "-p", str(DATA_DIR)],
        check=True,
    )

    print("Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(DATA_DIR)
    zip_path.unlink()
    print(f"Done. Data in {DATA_DIR}")


if __name__ == "__main__":
    download()
