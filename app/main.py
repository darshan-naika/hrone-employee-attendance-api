"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
import calendar
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient


def make_json_safe(value):
    from datetime import datetime, date
    from bson import Timestamp, ObjectId

    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]
    if isinstance(value, Timestamp):
        return {"time": value.time, "increment": value.inc}
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

# Create indexes for employee lookups and attendance queries.
db.employees.create_index(
    [("emp_code", 1)],
    unique=True,
    name="uq_employee_code"
)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True,
    name="uq_attendance_employee_date"
)

db.attendance_logs.create_index(
    [("date", 1), ("status", 1), ("emp_code", 1)],
    name="idx_attendance_date_status_employee"
)

db.attendance_logs.create_index(
    [("emp_code", 1), ("punch_in", -1)],
    name="ix_attendance_emp_punchin"
)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def truncate_to_seconds(value: datetime) -> datetime:
    """Remove fractional seconds from a datetime."""
    return value.replace(microsecond=0)


def as_ist(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC; convert aware datetimes to IST."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def epoch_ms_to_ist(value: int) -> datetime:
    """Convert epoch milliseconds into a whole-second IST datetime."""
    instant = datetime.fromtimestamp(value / 1000, tz=UTC)
    return truncate_to_seconds(instant).astimezone(IST)


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    local_in = as_ist(punch_in)
    hours, minutes = map(int, shift_start.split(":"))
    start = local_in.replace(
        hour=hours, minute=minutes, second=0, microsecond=0
    )
    elapsed_seconds = int((local_in - start).total_seconds())

    if elapsed_seconds <= 600:
        return 0

    return elapsed_seconds // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    start = as_ist(punch_in)
    end = as_ist(punch_out)
    seconds = int((end - start).total_seconds())

    hours = Decimal(seconds) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))



def compute_overtime(
    punch_out: datetime, shift_end: str, date_str: str
) -> int:
    local_out = as_ist(punch_out)
    attendance_day = date.fromisoformat(date_str)

    start_h, start_m = map(int, "00:00".split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    shift_start = datetime(
        attendance_day.year,
        attendance_day.month,
        attendance_day.day,
        0,
        0,
        tzinfo=IST,
    )

    shift_end_dt = shift_start.replace(
        hour=end_h,
        minute=end_m,
    )

    # Overnight shift: its end is on the following calendar day.
    if shift_end <= "09:30":
        shift_end_dt += timedelta(days=1)

    elapsed_seconds = int((local_out - shift_end_dt).total_seconds())
    overtime_minutes = elapsed_seconds // 60

    return overtime_minutes if overtime_minutes >= 30 else 0




# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    from pymongo.errors import DuplicateKeyError

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        ts = epoch_ms_to_ist(body.punched_at)
    else:
        ts = truncate_to_seconds(datetime.now(IST))

    # Find the latest open attendance record whose punch-in
    # occurred no later than the requested punch-out instant.
    punch_out_utc = ts.astimezone(UTC)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_utc},
            "punch_out": None,
            "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No open attendance record found",
        )

    punch_in_value = record["punch_in"]
    if punch_in_value.tzinfo is None:
        punch_in_value = punch_in_value.replace(tzinfo=UTC)

    elapsed = (punch_out_utc - punch_in_value).total_seconds()

    if elapsed <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if elapsed > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    work_hours = compute_work_hours(punch_in_value, punch_out_utc)
    half_day = work_hours < 4.50
    overtime_minutes = compute_overtime(
        ts, emp["shift_end"], record["date"]
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_utc,
                "work_hours": work_hours,
                "half_day": half_day,
                "overtime_minutes": overtime_minutes,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed; please retry",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    updated["punch_in"] = int(
        punch_in_value.timestamp() * 1000
    )
    updated["punch_out"] = int(punch_out_utc.timestamp() * 1000)

    return updated


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    query = {}
    if department:
        query["department"] = department

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    if body.punched_at is not None:
        ts = epoch_ms_to_ist(body.punched_at)
    else:
        ts = truncate_to_seconds(datetime.now(IST))

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # For overnight shifts, early-morning punch-ins belong to the previous day.
    attendance_day = ts.date()
    if shift_end <= shift_start and ts.strftime("%H:%M") < shift_end:
        attendance_day -= timedelta(days=1)

    date_str = attendance_day.isoformat()

    # MongoDB stores instants in UTC.
    punch_in_utc = ts.astimezone(UTC)

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, shift_start),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except Exception as exc:
        # Handle a duplicate attendance record if a unique index exists.
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="Attendance already exists for this date",
            )
        raise

    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)

    # Return timestamps as epoch milliseconds.
    doc["punch_in"] = int(ts.timestamp() * 1000)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(emp_code: str, month: str):
    import re
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    try:
        year, month_num = map(int, month.split("-"))
        first_day = date(year, month_num, 1)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid month",
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    last_day_num = calendar.monthrange(year, month_num)[1]
    last_day = date(year, month_num, last_day_num)

    joined_on = date.fromisoformat(employee["joined_on"])
    working_days = 0
    current = max(first_day, joined_on)

    while current <= last_day:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    logs = list(
        db.attendance_logs.find(
            {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
            },
            {"_id": 0},
        )
    )

    present_days = 0.0
    leave_days = 0
    late_count = 0

    for log in logs:
        status = log.get("status")

        if status in {"PRESENT", "WFH", "ON_DUTY"}:
            if date.fromisoformat(log["date"]).weekday() < 5:
                present_days += 0.5 if log.get("half_day", False) else 1.0
        elif status == "LEAVE":
            leave_days += 1

        if log.get("late_minutes", 0) > 0:
            late_count += 1

    attendance_pct = None
    if working_days > 0:
        pct = Decimal(str(present_days)) / Decimal(working_days) * 100
        attendance_pct = float(
            pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    import re
    from datetime import date
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    try:
        year, month_num = map(int, month.split("-"))
        first_day = date(year, month_num, 1)
        last_day = date(
            year,
            month_num,
            calendar.monthrange(year, month_num)[1]
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid month"
        )

    period_end = last_day.isoformat()

    employee_match = {"joined_on": {"$lte": period_end}}
    if department:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", first_day.isoformat()]},
                                    {"$lte": ["$date", period_end]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": 1,
                "emp_code": 1,
                "logs": 1
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "logs": {"$push": "$logs"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "logs": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": [],
                        "in": {"$concatArrays": ["$$value", "$$this"]}
                    }
                }
            }
        },
        {
            "$project": {
                "department": 1,
                "headcount": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$lt": [{"$dayOfWeek": {
                                        "date": {
                                            "$dateFromString": {
                                                "dateString": "$$log.date"
                                            }
                                        }
                                    }}, 7]},
                                    {
                                        "$cond": [
                                            {"$eq": ["$$log.half_day", True]},
                                            0.5,
                                            1
                                        ]
                                    },
                                    0
                                ]
                            }
                        }
                    }
                },
                "avg_work_hours": {
                    "$avg": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {"$in": [
                                                "$$log.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]},
                                            {"$ne": ["$$log.work_hours", None]}
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": "$$log.work_hours"
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "leave_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]}
                        }
                    }
                },
                "on_duty_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "ON_DUTY"]}
                        }
                    }
                }
            }
        },
        {"$sort": {"department": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))

    for item in results:
        if item.get("avg_work_hours") is not None:
            value = Decimal(str(item["avg_work_hours"]))
            item["avg_work_hours"] = float(
                value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )

    return {"month": month, "items": results}


@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    import re

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    try:
        year, month_num = map(int, month.split("-"))
        first_day = date(year, month_num, 1).isoformat()
        last_day = date(
            year,
            month_num,
            calendar.monthrange(year, month_num)[1]
        ).isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 100"
        )

    employee_match = {}
    if department:
        employee_match["department"] = department

    pipeline = [
        {
            "$match": {
                "date": {"$gte": first_day, "$lte": last_day},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {"$match": {
            **({"employee.department": department} if department else {})
        }},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1
            }
        }
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "limit": limit,
        "department": department,
        "items": items
    }



@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: str = __import__("fastapi").Query(alias="from"),
    to_date: str = __import__("fastapi").Query(alias="to")
):
    from datetime import date as date_type, datetime as datetime_type, timezone as timezone_type

    # Validate the date parameters.
    try:
        start_day = date_type.fromisoformat(from_date)
        end_day = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if start_day.isoformat() != from_date or end_day.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from"
        )

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    start_datetime = datetime_type.combine(
        start_day, datetime_type.min.time(), tzinfo=timezone_type.utc
    )
    end_datetime_exclusive = datetime_type.combine(
        end_day + timedelta(days=1),
        datetime_type.min.time(),
        tzinfo=timezone_type.utc
    )

    pipeline = [
        {
            "$documents": [
                {"date": start_datetime}
            ]
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        start_datetime,
                        end_datetime_exclusive
                    ]
                }
            }
        },
        {
            "$set": {
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC"
                    }
                },
                "weekday": {"$dayOfWeek": "$date"}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$day"]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "emp_code": 1
                        }
                    }
                ],
                "as": "eligible_employees"
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$eligible_employees"},
                "employee_codes": {
                    "$map": {
                        "input": "$eligible_employees",
                        "as": "employee",
                        "in": "$$employee.emp_code"
                    }
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$weekday", 2]},
                        {"$lte": ["$weekday", 6]}
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day": "$date_string",
                    "codes": "$employee_codes"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {"$in": ["$emp_code", "$$codes"]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$set": {
                "present_count": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$this.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$$this.half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            ]
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0
                                ]
                            }
                        }
                    }
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$round": [
                                {"$divide": ["$present_count", "$headcount"]},
                                4
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$round": ["$moving_avg_7d", 4]
                }
            }
        },
        {"$sort": {"date": 1}}
    ]

    try:
        items = list(db.aggregate(pipeline))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Trend aggregation failed: {str(exc)}"
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": items
    }


@app.get("/admin/explain/{endpoint}")
def explain_query(endpoint: str):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend"
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail=f"endpoint must be one of: {', '.join(sorted(allowed_endpoints))}"
        )

    try:
        if endpoint == "attendance_list":
            result = db.command({
                "explain": {
                    "find": "attendance_logs",
                    "filter": {"date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}},
                    "sort": {"date": 1, "emp_code": 1}
                },
                "verbosity": "executionStats"
            })

        elif endpoint == "employee_monthly":
            result = db.command({
                "explain": {
                    "find": "attendance_logs",
                    "filter": {
                        "emp_code": "EMP1001",
                        "date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}
                    }
                },
                "verbosity": "executionStats"
            })

        elif endpoint == "department_summary":
            result = db.command({
                "explain": {
                    "find": "employees",
                    "filter": {"joined_on": {"$lte": "2026-10-31"}},
                    "sort": {"department": 1}
                },
                "verbosity": "executionStats"
            })

        elif endpoint == "late_leaderboard":
            result = db.command({
                "explain": {
                    "find": "attendance_logs",
                    "filter": {
                        "date": {"$gte": "2026-10-01", "$lte": "2026-10-31"},
                        "late_minutes": {"$gt": 0}
                    },
                    "sort": {"late_minutes": -1, "emp_code": 1}
                },
                "verbosity": "executionStats"
            })

        else:
            result = db.command({
                "explain": {
                    "aggregate": "attendance_logs",
                    "pipeline": [
                        {"$match": {
                            "date": {"$gte": "2026-10-01", "$lte": "2026-10-31"}
                        }},
                        {"$limit": 100}
                    ],
                    "cursor": {}
                },
                "verbosity": "executionStats"
            })

        return make_json_safe(result)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Explain failed: {str(exc)}"
        )

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
