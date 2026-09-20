"""The migration history is a chain, not a fork.

Two branches that each add a migration can pick the same revision id, or hang two revisions off
the same parent. Alembic then refuses `upgrade head` as ambiguous — and because the deploy runs
`alembic upgrade head` before the app starts (railway.json preDeployCommand), the release fails
at boot rather than in review. This test is cheap, needs no database, and would have caught it.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _scripts() -> ScriptDirectory:
    backend = Path(__file__).parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    return ScriptDirectory.from_config(config)


def test_the_migration_history_has_exactly_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, f"migrations forked into {len(heads)} heads: {heads}"


def test_every_revision_id_is_unique():
    ids = [script.revision for script in _scripts().walk_revisions()]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"revision ids reused: {sorted(duplicates)}"
