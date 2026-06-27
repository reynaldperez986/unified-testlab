import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta

from django.db import close_old_connections

from .models import TestCase, TestExecution
from .services import execute_test_case


class ExecutionManager:
    TERMINAL_STATUSES = {"PASS", "FAIL", "ERROR", "STOPPED"}

    def __init__(self):
        self._lock = threading.RLock()
        self._runs = {}

    def start_run(self, test_case_ids, mode, requested_by):
        run_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        jobs = []

        for case_id in test_case_ids:
            jobs.append(
                {
                    "id": str(uuid.uuid4()),
                    "test_case_id": case_id,
                    "name": f"TestCase #{case_id}",
                    "status": "QUEUED",
                    "details": "",
                    "actual": "",
                    "paused": False,
                    "stop_requested": False,
                    "started_at": None,
                    "finished_at": None,
                }
            )

        run = {
            "id": run_id,
            "mode": mode,
            "status": "RUNNING",
            "requested_by": requested_by,
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "paused": False,
            "stop_all": False,
            "jobs": jobs,
        }

        with self._lock:
            self._runs[run_id] = run
            self._prune_runs()

        thread = threading.Thread(target=self._execute_run, args=(run_id,), daemon=True)
        thread.start()

        return run_id

    def get_state(self):
        with self._lock:
            self._prune_runs()
            runs = []
            for run in self._runs.values():
                completed = sum(1 for j in run["jobs"] if j["status"] in self.TERMINAL_STATUSES)
                runs.append(
                    {
                        "id": run["id"],
                        "mode": run["mode"],
                        "status": run["status"],
                        "paused": run["paused"],
                        "created_at": run["created_at"],
                        "requested_by": run["requested_by"],
                        "total": len(run["jobs"]),
                        "completed": completed,
                        "jobs": [
                            {
                                "id": job["id"],
                                "name": job["name"],
                                "status": job["status"],
                                "details": job["details"],
                            }
                            for job in run["jobs"]
                        ],
                    }
                )
            return sorted(runs, key=lambda x: x["created_at"], reverse=True)

    def toggle_run_pause(self, run_id):
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False, "Run not found."
            if run["status"] in {"COMPLETED", "STOPPED"}:
                return False, "Run is already finished."
            run["paused"] = not run["paused"]
            for job in run["jobs"]:
                if job["status"] == "QUEUED" and run["paused"]:
                    job["status"] = "PAUSED"
                elif job["status"] == "PAUSED" and not run["paused"] and not job["stop_requested"]:
                    job["status"] = "QUEUED"
            return True, "Run paused." if run["paused"] else "Run resumed."

    def stop_run(self, run_id):
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False, "Run not found."
            run["stop_all"] = True
            run["paused"] = False
            for job in run["jobs"]:
                if job["status"] in {"QUEUED", "PAUSED"}:
                    job["stop_requested"] = True
                    job["status"] = "STOPPED"
                    job["details"] = "Stopped before execution."
                    job["finished_at"] = datetime.utcnow().isoformat()
            return True, "Stop requested for all jobs."

    def toggle_job_pause(self, job_id):
        with self._lock:
            run, job = self._find_job(job_id)
            if not run or not job:
                return False, "Job not found."
            if job["status"] == "RUNNING":
                return False, "Running job cannot be paused immediately."
            if job["status"] in self.TERMINAL_STATUSES:
                return False, "Job is already finished."
            job["paused"] = not job["paused"]
            job["status"] = "PAUSED" if job["paused"] else "QUEUED"
            return True, "Job paused." if job["paused"] else "Job resumed."

    def stop_job(self, job_id):
        with self._lock:
            run, job = self._find_job(job_id)
            if not run or not job:
                return False, "Job not found."
            job["stop_requested"] = True
            if job["status"] in {"QUEUED", "PAUSED"}:
                job["status"] = "STOPPED"
                job["details"] = "Stopped before execution."
                job["finished_at"] = datetime.utcnow().isoformat()
            return True, "Stop requested for job."

    def _execute_run(self, run_id):
        close_old_connections()
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            mode = run["mode"]
            jobs = list(run["jobs"])

        if mode == "parallel":
            self._run_parallel(run_id, jobs)
        else:
            self._run_serial(run_id, jobs)

        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            unfinished = [j for j in run["jobs"] if j["status"] not in self.TERMINAL_STATUSES]
            if not unfinished:
                if any(j["status"] == "STOPPED" for j in run["jobs"]):
                    run["status"] = "STOPPED"
                else:
                    run["status"] = "COMPLETED"
                run["finished_at"] = datetime.utcnow().isoformat()

    def _run_serial(self, run_id, jobs):
        for job in jobs:
            if not self._should_execute_job(run_id, job["id"]):
                continue
            self._execute_single_job(run_id, job["id"])

    def _run_parallel(self, run_id, jobs):
        max_workers = min(6, max(1, len(jobs)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for job in jobs:
                futures.append(executor.submit(self._execute_single_job, run_id, job["id"]))
            wait(futures)

    def _should_execute_job(self, run_id, job_id):
        while True:
            with self._lock:
                run = self._runs.get(run_id)
                if not run:
                    return False
                job = self._find_job_in_run(run, job_id)
                if not job:
                    return False

                if run["stop_all"] or job["stop_requested"]:
                    if job["status"] not in self.TERMINAL_STATUSES:
                        job["status"] = "STOPPED"
                        job["details"] = "Stopped before execution."
                        job["finished_at"] = datetime.utcnow().isoformat()
                    return False

                if run["paused"] or job["paused"]:
                    if job["status"] == "QUEUED":
                        job["status"] = "PAUSED"
                else:
                    if job["status"] == "PAUSED":
                        job["status"] = "QUEUED"
                    return True

            time.sleep(0.25)

    def _execute_single_job(self, run_id, job_id):
        if not self._should_execute_job(run_id, job_id):
            return

        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            job = self._find_job_in_run(run, job_id)
            if not job:
                return
            job["status"] = "RUNNING"
            job["started_at"] = datetime.utcnow().isoformat()

        close_old_connections()
        test_case = TestCase.objects.select_related("connection").filter(pk=job["test_case_id"]).first()
        if not test_case:
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    job = self._find_job_in_run(run, job_id)
                    if job:
                        job["status"] = "ERROR"
                        job["details"] = "Test case no longer exists."
                        job["finished_at"] = datetime.utcnow().isoformat()
            return

        with self._lock:
            run = self._runs.get(run_id)
            if run:
                job = self._find_job_in_run(run, job_id)
                if job:
                    job["name"] = test_case.name

        status, details, actual = execute_test_case(test_case)

        if status in {"PASS", "FAIL", "ERROR"}:
            TestExecution.objects.create(
                test_case=test_case,
                status=status,
                details=details,
                actual_value=actual,
            )

        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            job = self._find_job_in_run(run, job_id)
            if not job:
                return

            if run["stop_all"] or job["stop_requested"]:
                job["status"] = "STOPPED"
                job["details"] = "Stop requested. Current step finished."
                job["actual"] = actual
            else:
                job["status"] = status
                job["details"] = details
                job["actual"] = actual
            job["finished_at"] = datetime.utcnow().isoformat()

    def _find_job(self, job_id):
        for run in self._runs.values():
            for job in run["jobs"]:
                if job["id"] == job_id:
                    return run, job
        return None, None

    def _find_job_in_run(self, run, job_id):
        for job in run["jobs"]:
            if job["id"] == job_id:
                return job
        return None

    def _prune_runs(self):
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        removable = []
        for run_id, run in self._runs.items():
            finished = run.get("finished_at")
            if not finished:
                continue
            try:
                finished_dt = datetime.fromisoformat(finished)
            except ValueError:
                continue
            if finished_dt < cutoff:
                removable.append(run_id)
        for run_id in removable:
            self._runs.pop(run_id, None)


execution_manager = ExecutionManager()
