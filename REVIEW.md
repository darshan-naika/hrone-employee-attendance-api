# REVIEW.md

I reviewed the starter implementation against the API requirements and checked the relevant code paths.

| # | Where | What is wrong | How to notice | How it was fixed |
|---|---|---|---|---|
| 1 | `GET /attendance` | Pagination is not validated, and all matching records are loaded into Python before sorting and slicing. | Try an invalid page or a large dataset. | Validate pagination and perform filtering, sorting, counting, and pagination in MongoDB. |

| 2 | `GET /attendance` | Returned attendance records can expose MongoDB fields and datetime values in a format inconsistent with the API contract. | Inspect the response for `_id` and punch timestamps. | Remove internal IDs and serialize timestamps as epoch milliseconds. |

| 3 | Employee monthly analytics | Attendance logs are fetched and processed in Python instead of using MongoDB aggregation as required. | Inspect the implementation and test with a large number of logs. | Use a MongoDB aggregation pipeline for log-based calculations. |

| 4 | Department summary | The weekday calculation treats Sunday as a working day because `$dayOfWeek` returns Sunday as `1` and Saturday as `7`. | Add a Sunday attendance record and inspect `present_days`. | Count only Monday through Friday. |

| 5 | Punch-out overtime helper | Overnight shift handling is inferred from a fixed `09:30` cutoff instead of the employee's actual shift start and end. | Test overnight shifts with different shift times. | Calculate the actual shift-end instant using the employee's shift schedule and attendance date. |

| 6 | `GET /admin/explain/{endpoint}` | The explain queries use hard-coded sample dates and simplified query shapes rather than the actual endpoint query or aggregation. | Compare explain output with the endpoint's filters and pipeline. | Align explain operations with the real query shapes and relevant filters. |

## Other observations

The unique employee-code index and unique `(emp_code, date)` attendance index provide important database-level protections. Duplicate-key errors must be handled because checking for an existing record before insertion alone cannot prevent simultaneous requests from creating duplicates.

Before submission, verify each listed fix against the final implementation and add any other confirmed defects discovered during testing.