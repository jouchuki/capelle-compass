import os

MUNICIPALITY = "capelleaandenijssel"
OUTPUT_DIR   = os.getenv("OUTPUT_DIR", "output")
CSV_PATH     = os.path.join(OUTPUT_DIR, "reports.csv")
