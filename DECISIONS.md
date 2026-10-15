# DECISIONS.md

1. **Indexes.** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, and a unique compound index on `attendance_logs.emp_code` and `date` to allow only one attendance record per employee per day. I also added indexes for attendance date/status filtering and employee punch-in lookup. I would consider a department and joined-date index for department reporting, depending on query plans and data distribution.

2. **Punch-in race.** Both requests may initially find no existing record. The unique compound index is the final safeguard: only one insert can succeed. The other request should receive HTTP 409 Conflict when MongoDB reports a duplicate key.

3. **Ties.** The leaderboard uses MongoDB ranking so employees with equal late minutes share a rank, and the next rank is skipped. The limit is applied after ranking, so tied employees at the cutoff can all be returned.

4. **Headcount.** The department summary starts from employees and joins attendance records, rather than starting only from attendance logs. This allows employees with no logs to contribute to headcount if they joined by the period end.

5. **100x the data.** I would inspect execution statistics on realistic data, review index selectivity, and optimize expensive aggregation stages. I would also add automated tests for edge cases and monitor query latency before introducing caching or other infrastructure.