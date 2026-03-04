from __future__ import annotations

from datetime import datetime
from pathlib import Path
import csv
import json


DEFAULT_SHIFTS = [
    {
        "medic_id": 10452,
        "date": "2026-03-03",
        "shift_start": "09:00",
        "shift_end": "17:00",
        "station": "WIMTACH Unit 4012",
        "partner": "Medic 10309",
        "break_window": "13:00-13:30",
    },
    {
        "medic_id": 10452,
        "date": "2026-03-04",
        "shift_start": "09:00",
        "shift_end": "17:00",
        "station": "WIMTACH Unit 4012",
        "partner": "Medic 10309",
        "break_window": "12:30-13:00",
    },
]


def _load_csv_if_present() -> list[dict]:
    csv_path = Path(__file__).resolve().parent / "data" / "shift_schedule.csv"
    if not csv_path.exists():
        return DEFAULT_SHIFTS

    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["medic_id"] = int(row["medic_id"])
            rows.append(row)
    return rows or DEFAULT_SHIFTS


def check_schedule(medic_id: int, target_date: str | None = None) -> str:
    all_rows = _load_csv_if_present()
    date_filter = target_date or datetime.now().strftime("%Y-%m-%d")

    filtered = [
        row for row in all_rows if int(row.get("medic_id", -1)) == int(medic_id) and row.get("date") == date_filter
    ]

    if not filtered:
        fallback = [row for row in all_rows if int(row.get("medic_id", -1)) == int(medic_id)]
        if fallback:
            return json.dumps(
                {
                    "found": False,
                    "message": f"No shift found on {date_filter}. Returning upcoming known schedule.",
                    "schedule": fallback[:2],
                }
            )

        return json.dumps(
            {
                "found": False,
                "message": f"No schedule found for medic_id {medic_id}.",
                "schedule": [],
            }
        )

    return json.dumps(
        {
            "found": True,
            "message": f"Schedule found for medic_id {medic_id} on {date_filter}.",
            "schedule": filtered,
        }
    )