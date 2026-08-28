1. **Understand the Goal**: The objective is to add tests for the `format_worker_status` function in `src/nexus_scalp/accounting/worker.py`.
2. **Review Existing Code**: `format_worker_status` takes an `AccountingWorker` instance and extracts its state (e.g., `running`, `cycle_count`, `interval_sec`, `last_cycle_start`, `last_cycle_duration`, and `last_error`) to create a dictionary.
3. **Plan Test Cases**: Add a new test method to `tests/unit/test_accounting_core.py` (specifically under the class `TestAccountingWorker`).
    - **Test 1**: Verify `format_worker_status` when the worker is just initialized (no cycles run, `last_cycle_start` and `last_cycle_duration` are `None` or falsy).
    - **Test 2**: Verify `format_worker_status` after a cycle has run (where `last_cycle_start` has a datetime, `last_cycle_duration` has a float, and `last_error` is populated or empty).
    - **Test 3**: Verify `format_worker_status` handling errors (where `last_error` is a string).
4. **Implement**:
    - Write a function `test_format_worker_status(self, core)` inside `TestAccountingWorker`.
    - Instantiate an `AccountingWorker`.
    - Call `format_worker_status` on the unstarted worker. Assert the expected dictionary values (e.g., `"status": "IDLE"`, `"last_cycle_start": None`, etc.).
    - Mock or manually set `worker.running = True`, `worker.cycle_count = 5`, `worker.last_cycle_start = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)`, `worker.last_cycle_duration = 1.234`, `worker.last_error = "Some error"`.
    - Call `format_worker_status` again. Assert the expected dictionary values (e.g., `"status": "RUNNING"`, `"last_cycle_start": "2023-01-01T12:00:00+00:00"`, `"last_cycle_duration_ms": 1234.0`, etc.).
5. **Pre-commit**: Complete pre commit steps to ensure proper testing, verification, review, and reflection are done.
6. **Submit**: Create PR.
