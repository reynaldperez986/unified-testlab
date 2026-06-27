"""
Shared utility helpers for the recorder app.

These can be safely imported from replay engines, management commands, etc.
without creating circular dependencies with views.py.
"""

import logging

logger = logging.getLogger(__name__)


def update_primary_locators_from_stats(run_id: str) -> int:
    """
    After a run completes, recalculate ``is_primary`` for every locator whose
    ``(record_id, step_no)`` appeared in *that* run.

    Algorithm
    ---------
    * Collect all ``(record_id, step_no)`` pairs that appear in
      ``locators_stat`` for ``run_id``.
    * For each such pair, aggregate **all-time** hit counts from
      ``locators_stat`` (not just this run) grouped by
      ``(strategy, locator)``.
    * The strategy+locator with the most hits wins ``is_primary = TRUE``.
      Ties are broken by the lowest ``locator_rank``.
    * Every locator row in ``locators`` for those steps — including locators
      that never appeared in ``locators_stat`` — is updated:
      winners get ``TRUE``, all others get ``FALSE``.

    Returns the number of rows updated in ``locators``.
    """
    from django.db import connection

    sql = """
        WITH this_run_steps AS (
            -- Steps that actually appeared in this run's stat records
            SELECT DISTINCT record_id, step_no
            FROM   locators_stat
            WHERE  run_id = %(run_id)s
        ),
        hit_counts AS (
            -- All-time successful hits for those steps (exclude coordinate
            -- fallback which has no corresponding row in the locators table)
            SELECT ls.record_id,
                   ls.step_no,
                   ls.strategy,
                   ls.locator,
                   COUNT(*)             AS hit_count,
                   MIN(ls.locator_rank) AS best_rank
            FROM   locators_stat ls
            JOIN   this_run_steps t
                     ON  t.record_id = ls.record_id
                     AND t.step_no   = ls.step_no
            WHERE  ls.strategy <> 'coordinates'
            GROUP BY ls.record_id, ls.step_no, ls.strategy, ls.locator
        ),
        winners AS (
            -- Rank candidates: most hits first, then lowest locator_rank
            SELECT record_id,
                   step_no,
                   strategy,
                   locator,
                   ROW_NUMBER() OVER (
                       PARTITION BY record_id, step_no
                       ORDER BY hit_count DESC, best_rank ASC NULLS LAST
                   ) AS rn
            FROM   hit_counts
        ),
        steps_with_real_stats AS (
            -- Only update locators for steps that have at least one non-
            -- coordinate stat entry; otherwise preserve existing is_primary.
            SELECT DISTINCT record_id, step_no FROM hit_counts
        )
        UPDATE locators l
        SET    is_primary = COALESCE(
                   (
                       SELECT (w.rn = 1)
                       FROM   winners w
                       WHERE  w.record_id = l.record_id
                         AND  w.step_no   = l.step_no
                         AND  w.strategy  = l.strategy
                         AND  w.locator   = l.locator
                       LIMIT  1
                   ),
                   FALSE   -- locators with no stat history become non-primary
               )
        FROM   steps_with_real_stats srs
        WHERE  l.record_id = srs.record_id
          AND  l.step_no   = srs.step_no
    """
    try:
        with connection.cursor() as cur:
            cur.execute(sql, {"run_id": run_id})
            updated = cur.rowcount
        logger.info(
            "update_primary_locators_from_stats  run_id=%s  rows_updated=%d",
            run_id,
            updated,
        )
        return updated
    except Exception as exc:
        logger.exception(
            "update_primary_locators_from_stats  run_id=%s  FAILED: %s",
            run_id,
            exc,
        )
        return 0
