"""
Management command: seed_burndown

Inserts random RunResult rows into run_table to populate the
Run Results Burndown chart with 30 days of synthetic data.

Usage:
    python manage.py seed_burndown           # 30 days, default profile
    python manage.py seed_burndown --days 14
    python manage.py seed_burndown --clear   # delete seeded rows first
"""
import uuid
import random
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from recorder.models import RunResult

# Plausible action names for synthetic steps
ACTIONS = [
    "click", "type", "navigate", "assert_text", "assert_visible",
    "wait", "select", "hover", "scroll", "screenshot",
    "assert_url", "submit", "upload", "drag_and_drop", "keypress",
]

PAGES = [
    "https://app.example.com/login",
    "https://app.example.com/dashboard",
    "https://app.example.com/settings",
    "https://app.example.com/reports",
    "https://app.example.com/profile",
]

# Synthetic folder / record UUIDs kept stable so re-runs are idempotent
FOLDERS = [
    uuid.UUID("aaaaaaaa-0001-0001-0001-000000000001"),
    uuid.UUID("aaaaaaaa-0001-0001-0001-000000000002"),
    uuid.UUID("aaaaaaaa-0001-0001-0001-000000000003"),
]

SEED_RUNNER = "__seed_burndown__"  # marker so --clear can target only our rows


class Command(BaseCommand):
    help = "Seed run_table with 30 days of random burndown data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=30,
            help="Number of past days to fill (default: 30).",
        )
        parser.add_argument(
            "--runs-per-day", type=int, default=None,
            help="Fixed runs per day (default: random 3–8).",
        )
        parser.add_argument(
            "--steps-per-run", type=int, default=None,
            help="Fixed steps per run (default: random 5–20).",
        )
        parser.add_argument(
            "--pass-rate", type=float, default=0.72,
            help="Probability a step passes (0–1, default: 0.72).",
        )
        parser.add_argument(
            "--fail-rate", type=float, default=0.18,
            help="Probability a step fails (0–1, default: 0.18; rest = not_executed).",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete previously seeded rows before inserting.",
        )

    def handle(self, *args, **options):
        days       = options["days"]
        rpd        = options["runs_per_day"]
        spr        = options["steps_per_run"]
        pass_rate  = max(0.0, min(1.0, options["pass_rate"]))
        fail_rate  = max(0.0, min(1.0 - pass_rate, options["fail_rate"]))

        if options["clear"]:
            deleted, _ = RunResult.objects.filter(runner=SEED_RUNNER).delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} seeded rows."))

        now   = timezone.now()
        rows  = []
        total = 0

        for day_offset in range(days - 1, -1, -1):
            day_dt = now - datetime.timedelta(days=day_offset)
            day_dt = day_dt.replace(hour=0, minute=0, second=0, microsecond=0)

            num_runs = rpd if rpd else random.randint(3, 8)
            folder   = random.choice(FOLDERS)

            for _ in range(num_runs):
                run_id    = uuid.uuid4()
                record_id = uuid.uuid4()
                num_steps = spr if spr else random.randint(5, 20)

                # Inject a realistic run_date (spread within the day)
                run_hour   = random.randint(7, 21)
                run_minute = random.randint(0, 59)
                run_dt     = day_dt.replace(hour=run_hour, minute=run_minute)

                # Random run profile: mostly-passing, mostly-failing, or mixed
                profile = random.random()
                if profile < 0.55:      # healthy run
                    p, f = pass_rate, fail_rate
                elif profile < 0.75:    # flaky run
                    p, f = pass_rate * 0.60, fail_rate * 1.6
                else:                   # struggling run
                    p, f = pass_rate * 0.30, min(fail_rate * 2.5, 1 - pass_rate * 0.30)

                # Decide session-level outcome first so that ~pass_rate of
                # sessions have ZERO failures (which is what the stat chips count).
                session_roll = random.random()
                if session_roll < p:
                    # All-pass session — every step passes
                    session_bucket = "all_pass"
                elif session_roll < p + f:
                    # Failing session — inject some fail steps
                    session_bucket = "fail"
                else:
                    session_bucket = "not_exec"

                for step_no in range(1, num_steps + 1):
                    if session_bucket == "all_pass":
                        status = RunResult.STATUS_PASS
                    elif session_bucket == "fail":
                        # ~60% of steps fail, rest pass
                        status = RunResult.STATUS_FAIL if random.random() < 0.60 else RunResult.STATUS_PASS
                    else:
                        # not_exec session: steps are not_executed or pass, no fails
                        status = RunResult.STATUS_NOT_EXECUTED if random.random() < 0.70 else RunResult.STATUS_PASS

                    rows.append(RunResult(
                        run_id           = run_id,
                        record_id        = record_id,
                        step_no          = step_no,
                        action           = random.choice(ACTIONS),
                        page_url         = random.choice(PAGES),
                        raw_event        = {},
                        status           = status,
                        runner           = SEED_RUNNER,
                        parent_folder_id = folder,
                        run_date         = run_dt,
                    ))
                    total += 1

        # Bulk-create in batches of 500 to stay memory-friendly
        BATCH = 500
        created = 0
        for i in range(0, len(rows), BATCH):
            RunResult.objects.bulk_create(rows[i:i + BATCH])
            created += len(rows[i:i + BATCH])

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created} rows across {days} days "
                f"(pass≈{pass_rate:.0%}, fail≈{fail_rate:.0%})."
            )
        )
