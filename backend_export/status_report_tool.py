from __future__ import annotations

import json


STATUS_ITEMS = [
    {
        "item_type": "ACRc",
        "description": "ACR Completion",
        "status": "BAD",
        "issues": 2,
        "notes": "Each must be completed with 24 hours of call completion",
    },
    {
        "item_type": "ACEr",
        "description": "ACE Response",
        "status": "GOOD",
        "issues": 0,
        "notes": "Complete outstanding within 1 week of BH review",
    },
    {
        "item_type": "CERT-DL",
        "description": "Drivers License",
        "status": "GOOD",
        "issues": 0,
        "notes": "Drivers License Status",
    },
    {
        "item_type": "CERT-Va",
        "description": "Vaccinations",
        "status": "BAD",
        "issues": 1,
        "notes": "Vaccination Status as per guidelines",
    },
    {
        "item_type": "CERT-CE",
        "description": "Continuous Education Status",
        "status": "GOOD",
        "issues": 0,
        "notes": "CME outstanding",
    },
    {
        "item_type": "UNIF",
        "description": "Uniform credits",
        "status": "GOOD",
        "issues": 5,
        "notes": "Available Uniform order Credits",
    },
    {
        "item_type": "CRIM",
        "description": "Criminal Record Check",
        "status": "GOOD",
        "issues": 0,
        "notes": "Criminal Issue Free",
    },
    {
        "item_type": "ACP",
        "description": "ACP Status",
        "status": "GOOD",
        "issues": 0,
        "notes": "ACP Status is good if ACP",
    },
    {
        "item_type": "VAC",
        "description": "Vacation requested and approved",
        "status": "GOOD",
        "issues": 0,
        "notes": "Yearly vacation approved",
    },
    {
        "item_type": "MEALS",
        "description": "Missed Meal Claims",
        "status": "GOOD",
        "issues": 0,
        "notes": "Missed Meal Claims outstanding",
    },
    {
        "item_type": "OVER",
        "description": "Overtime Requests outstanding",
        "status": "BAD",
        "issues": 1,
        "notes": "Overtime claims outstanding",
    },
]


def check_status_report(item_type: str | None = None, status_filter: str | None = None, bad_only: bool = False) -> str:
    rows = STATUS_ITEMS

    if item_type:
        needle = item_type.strip().lower()
        rows = [
            row
            for row in rows
            if needle in row["item_type"].lower() or needle in row["description"].lower()
        ]

    if status_filter:
        status_needle = status_filter.strip().upper()
        rows = [row for row in rows if row["status"] == status_needle]

    if bad_only:
        rows = [row for row in rows if row["status"] == "BAD"]

    total_issues = sum(int(row["issues"]) for row in rows)

    return json.dumps(
        {
            "found": len(rows) > 0,
            "count": len(rows),
            "total_issues": total_issues,
            "rows": rows,
            "summary": {
                "bad_items": len([row for row in STATUS_ITEMS if row["status"] == "BAD"]),
                "bad_issue_total": sum(int(row["issues"]) for row in STATUS_ITEMS if row["status"] == "BAD"),
            },
        }
    )
