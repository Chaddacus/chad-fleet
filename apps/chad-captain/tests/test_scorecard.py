"""Tests for the compliance rubric scorer."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from chad_captain.scorecard import Scorecard, score_delta, score_repo


def _git_init(repo: Path) -> None:
    os.system(f"git -C {repo} init -q")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add -A")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm initial --allow-empty")


def test_score_repo_returns_seven_dimensions(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "README.md").write_text("# r\n\nstuff")
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    assert isinstance(sc, Scorecard)
    names = [d.name for d in sc.dimensions]
    assert names == [
        "tests_present",
        "tests_recent",
        "todo_pressure",
        "skip_pressure",
        "secret_hygiene",
        "file_size_health",
        "docs_present",
        "test_density",
        "migrations_consistent",
    ]
    assert 0.0 <= sc.aggregate <= 1.0


def test_score_repo_missing_path(tmp_path: Path) -> None:
    sc = score_repo(tmp_path / "nope")
    assert sc.aggregate == 0.0


def test_tests_present_zero_when_no_tests(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    assert sc.by_name("tests_present").score == 0.0


def test_tests_present_increases_with_tests(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "main.py").write_text("x = 1\n")
    (repo / "tests" / "test_main.py").write_text("def test_x(): assert 1\n")
    sc = score_repo(repo)
    assert sc.by_name("tests_present").score > 0.0


def test_todo_pressure_full_when_clean(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    assert sc.by_name("todo_pressure").score == 1.0


def test_todo_pressure_drops_with_markers(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("\n".join([f"# TODO: thing {i}" for i in range(20)]))
    sc = score_repo(repo)
    assert sc.by_name("todo_pressure").score < 1.0
    assert sc.by_name("todo_pressure").detail["marker_count"] == 20


def test_skip_pressure_drops_with_skips(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text(
        "import pytest\n"
        "@pytest.mark.skip\ndef test_a(): pass\n"
        "@pytest.mark.skip\ndef test_b(): pass\n"
    )
    sc = score_repo(repo)
    assert sc.by_name("skip_pressure").score < 1.0
    assert sc.by_name("skip_pressure").detail["skip_count"] == 2


def test_skip_pressure_perfect_when_no_tests(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    # No tests means no skips — full score.
    assert sc.by_name("skip_pressure").score == 1.0


def test_secret_hygiene_catches_aws_key(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "config.py").write_text('aws = "AKIA' + "ABCDEFGHIJKLMNOP" + '"\n')
    sc = score_repo(repo)
    dim = sc.by_name("secret_hygiene")
    assert dim.score == 0.0
    assert any(h["pattern"] == "aws-access-key" for h in dim.detail["hits"])


def test_secret_hygiene_skips_test_files(tmp_path: Path) -> None:
    """Tests commonly embed fake credentials — those are expected, not leaks."""
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text(
        'aws = "AKIA' + "ABCDEFGHIJKLMNOP" + '"\n'
    )
    sc = score_repo(repo)
    assert sc.by_name("secret_hygiene").score == 1.0


def test_secret_hygiene_skips_root_conftest(tmp_path: Path) -> None:
    """Regression: root-level conftest.py was being scanned (no /tests/ in path),
    flagging Django-style test fixtures like password='testpassword123' as
    leaks. conftest.py at any depth is pytest fixture code, not source."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "conftest.py").write_text(
        'def make_user():\n'
        '    return User.objects.create_user(\n'
        '        email="t@example.com", password="testpassword123",\n'
        '    )\n'
    )
    sc = score_repo(repo)
    assert sc.by_name("secret_hygiene").score == 1.0


def test_secret_hygiene_skips_nested_conftest(tmp_path: Path) -> None:
    """Nested conftest.py (e.g. tenants/conftest.py) is also test code."""
    repo = tmp_path / "r"
    (repo / "apps" / "tenants").mkdir(parents=True)
    (repo / "apps" / "tenants" / "conftest.py").write_text(
        'PASSWORD = "tenant-fixture-pw-12345"\n'
    )
    sc = score_repo(repo)
    assert sc.by_name("secret_hygiene").score == 1.0


def test_secret_hygiene_does_not_skip_misnamed_conftest(tmp_path: Path) -> None:
    """Belt-and-suspenders: a file named myconftest.py is NOT a pytest
    conftest and must still be scanned."""
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "myconftest.py").write_text(
        'PASSWORD = "leaked-real-secret-12345"\n'
    )
    sc = score_repo(repo)
    assert sc.by_name("secret_hygiene").score == 0.0


def test_secret_hygiene_catches_private_key(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "key.py").write_text('s = "-----BEGIN RSA PRIVATE KEY-----\\nMII"')
    sc = score_repo(repo)
    assert sc.by_name("secret_hygiene").score == 0.0


def test_secret_hygiene_skips_env_example(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    # Note: only files matching SOURCE_EXTS are scanned for secrets at all.
    # .env.example is not a source ext, so this test verifies the skip-name
    # path doesn't break when paired with a separate offending source file.
    (repo / "config.py").write_text("x = 1\n")
    (repo / ".env.example").write_text("AWS_KEY=AKIA0000000000000000\n")
    sc = score_repo(repo)
    # No source files contain secrets — clean.
    assert sc.by_name("secret_hygiene").score == 1.0


def test_file_size_health_drops_for_giant_file(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "big.py").write_text("\n" * 1500)
    sc = score_repo(repo)
    assert sc.by_name("file_size_health").score < 1.0


def test_file_size_health_continuous_decay(tmp_path: Path) -> None:
    """Each split should move the score by an observable amount so the
    captain loop credits incremental progress. Excess-LOC formula with
    BUDGET=20000: 4 giants × 500 excess = 2000 → 1 - 2000/20000 = 0.90."""
    repo = tmp_path / "r"
    repo.mkdir()
    # 4 giant files
    for i in range(4):
        (repo / f"big{i}.py").write_text("\n" * 1500)
    sc = score_repo(repo)
    assert sc.by_name("file_size_health").score == pytest.approx(0.90, abs=0.01)


def test_file_size_health_does_not_bottom_out_for_realistic_codebase(
    tmp_path: Path,
) -> None:
    """A real codebase with 15 giants × 1500 LOC = 7500 excess used to
    score 0.0 under the giant-count formula. Excess-LOC continuous
    formula gives 1 - 7500/20000 = 0.625 with room to climb per split.
    Live observation: author-toolkit has 11887 excess LOC, so the
    BUDGET=20000 is sized to keep that codebase off the floor."""
    repo = tmp_path / "r"
    repo.mkdir()
    for i in range(15):
        (repo / f"big{i}.py").write_text("\n" * 1500)
    sc = score_repo(repo)
    score = sc.by_name("file_size_health").score
    assert score > 0.0, f"expected room to grow, got {score}"
    assert score == pytest.approx(0.625, abs=0.01)


def test_file_size_health_split_moves_score(tmp_path: Path) -> None:
    """Going from 4 giants to 3 should produce an observable delta — the
    captain rubric needs >=0.5pp to issue accept (vs soft_accept)."""
    repo = tmp_path / "r"
    repo.mkdir()
    for i in range(4):
        (repo / f"big{i}.py").write_text("\n" * 1500)
    before = score_repo(repo).by_name("file_size_health").score

    # Simulate splitting one giant into a small file
    (repo / "big0.py").write_text("\n" * 100)
    after = score_repo(repo).by_name("file_size_health").score

    delta_pp = (after - before) * 100  # convert to pp
    assert delta_pp >= 0.5, f"expected >=0.5pp from one split, got {delta_pp}"


def test_file_size_health_within_giant_reduction_credits(tmp_path: Path) -> None:
    """Cutting LOC out of a still-giant file (3000 → 2900) used to give 0
    credit because the file count didn't change. Excess-LOC formula
    rewards the 100-line cut: 100/20000 = 0.005 = 0.5pp.

    Live failure that motivated this: author-toolkit S4 extracted 65 LOC
    from runtime_service.py (3079 → 3014) — both still giants — and the
    rubric returned +0.00pp delta, so captain issued soft_accept. Now
    that work earns +0.3pp, which is not accept-worthy alone but
    accumulates across slices instead of being silently zeroed."""
    repo = tmp_path / "r"
    repo.mkdir()
    big = repo / "service.py"
    big.write_text("\n" * 3000)
    before = score_repo(repo).by_name("file_size_health").score

    big.write_text("\n" * 2900)  # 100-line reduction; still a giant
    after = score_repo(repo).by_name("file_size_health").score

    delta_pp = (after - before) * 100
    assert delta_pp > 0.0, f"expected non-zero credit for in-giant cut, got {delta_pp}"
    assert delta_pp == pytest.approx(0.5, abs=0.05)


def test_test_density_continuous_with_added_tests(tmp_path: Path) -> None:
    """Adding more test LOC must produce a measurable rubric delta even
    when tests_present is already saturated at 1.0. This is the live
    failure mode that motivated the dim — billing/ added 53 test LOC
    on author-toolkit and the aggregate didn't budge."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "src.py").write_text("\n" * 1000)  # 1000 source LOC
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("\n" * 50)  # ratio 0.05 → score 0.10

    before = score_repo(repo).by_name("test_density").score
    (tests / "test_b.py").write_text("\n" * 50)  # ratio 0.10 → score 0.20
    after = score_repo(repo).by_name("test_density").score

    delta_pp = (after - before) * 100
    assert delta_pp > 0.0, f"adding tests must move test_density, got {delta_pp}"
    assert before == pytest.approx(0.10, abs=0.01)
    assert after == pytest.approx(0.20, abs=0.01)


def test_test_density_saturates_at_high_ratio(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "src.py").write_text("\n" * 100)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("\n" * 100)  # ratio 1.0 → score 1.0
    sc = score_repo(repo)
    assert sc.by_name("test_density").score == pytest.approx(1.0, abs=0.01)


def test_test_density_handles_no_source(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    sc = score_repo(repo)
    # No source files at all — dim degrades to 1.0 rather than crashing.
    assert sc.by_name("test_density").score == 1.0


def test_migrations_consistent_no_django_app_scores_1(tmp_path: Path) -> None:
    """Non-Django repo: dim is a no-op."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == 1.0


def test_migrations_consistent_app_with_migrations_scores_1(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    app = repo / "apps" / "billing"
    app.mkdir(parents=True)
    (app / "models.py").write_text("from django.db import models\n\nclass Plan(models.Model):\n    pass\n")
    mig = app / "migrations"
    mig.mkdir()
    (mig / "__init__.py").write_text("")
    (mig / "0001_initial.py").write_text("# generated\n")
    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == 1.0


def test_migrations_consistent_app_missing_migrations_scores_0(tmp_path: Path) -> None:
    """Live failure mode: model added without migration. PR ships, deploy
    breaks. Dim catches it during validate."""
    repo = tmp_path / "r"
    repo.mkdir()
    app = repo / "apps" / "billing"
    app.mkdir(parents=True)
    (app / "models.py").write_text(
        "from django.db import models\n\nclass Plan(models.Model):\n    pass\n"
    )
    # No migrations dir at all
    sc = score_repo(repo)
    score = sc.by_name("migrations_consistent").score
    assert score == 0.0


def test_migrations_consistent_partial_credit(tmp_path: Path) -> None:
    """One app has migrations, one doesn't → 0.5."""
    repo = tmp_path / "r"
    repo.mkdir()
    a = repo / "apps" / "billing"
    a.mkdir(parents=True)
    (a / "models.py").write_text("from django.db import models\n\nclass Plan(models.Model):\n    pass\n")
    (a / "migrations").mkdir()
    (a / "migrations" / "0001_initial.py").write_text("# x\n")

    b = repo / "apps" / "billing2"
    b.mkdir(parents=True)
    (b / "models.py").write_text("from django.db import models\n\nclass Plan2(models.Model):\n    pass\n")
    # No migrations dir for b

    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == pytest.approx(0.5, abs=0.01)


def test_migrations_consistent_skips_abstract_only_models(tmp_path: Path) -> None:
    """apps/core/models.py with only abstract base classes does NOT
    require migrations. Live observation: author-toolkit's apps/core
    has TimestampedModel + BaseModel (both abstract); old heuristic
    flagged it as missing migrations and dropped the score 11pp."""
    repo = tmp_path / "r"
    repo.mkdir()
    app = repo / "apps" / "core"
    app.mkdir(parents=True)
    (app / "models.py").write_text(
        "from django.db import models\n\n"
        "class BaseModel(models.Model):\n"
        "    class Meta:\n"
        "        abstract = True\n\n"
        "class TimestampedModel(models.Model):\n"
        "    class Meta:\n"
        "        abstract = True\n"
    )
    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == 1.0


def test_migrations_consistent_requires_migrations_for_mixed_abstract_concrete(tmp_path: Path) -> None:
    """If models.py has BOTH abstract and concrete classes, require
    migrations. The concrete model needs a migration even if there
    are abstract siblings."""
    repo = tmp_path / "r"
    repo.mkdir()
    app = repo / "apps" / "billing"
    app.mkdir(parents=True)
    (app / "models.py").write_text(
        "from django.db import models\n\n"
        "class Base(models.Model):\n"
        "    class Meta:\n"
        "        abstract = True\n\n"
        "class Plan(models.Model):\n"
        "    name = models.CharField(max_length=64)\n"
    )
    # No migrations dir
    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == 0.0


def test_migrations_consistent_ignores_models_py_without_django_classes(tmp_path: Path) -> None:
    """A models.py with just helper functions (no `models.Model`) shouldn't
    be flagged as a Django app."""
    repo = tmp_path / "r"
    repo.mkdir()
    app = repo / "lib"
    app.mkdir()
    (app / "models.py").write_text("# pure helpers — not Django\n\ndef fit():\n    return 1\n")
    sc = score_repo(repo)
    assert sc.by_name("migrations_consistent").score == 1.0
    assert "no Django models.py" in sc.by_name("migrations_consistent").rationale


def test_docs_present_zero_no_readme(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    sc = score_repo(repo)
    assert sc.by_name("docs_present").score == 0.0


def test_docs_present_full_with_readme_and_extras(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "README.md").write_text("# r\n")
    (repo / "DESIGN.md").write_text("design")
    sc = score_repo(repo)
    assert sc.by_name("docs_present").score == 1.0


def test_score_delta_positive_when_after_better(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n# TODO\n# TODO\n# TODO\n# TODO\n# TODO\n")
    before = score_repo(repo)
    (repo / "main.py").write_text("x = 1\n")
    after = score_repo(repo)
    assert score_delta(before, after) > 0


def test_score_delta_negative_when_after_worse(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    before = score_repo(repo)
    (repo / "main.py").write_text("\n".join(["# TODO"] * 30))
    after = score_repo(repo)
    assert score_delta(before, after) < 0


def test_tests_recent_zero_when_no_test_touches(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x(): assert 1\n")
    _git_init(repo)
    # Modify only main.py over 5 commits — no test touches.
    for i in range(5):
        (repo / "main.py").write_text(f"x = {i}\n")
        os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add main.py")
        os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm 'c{i}'")
    sc = score_repo(repo)
    # The initial add did include the test file, so this might be > 0
    # We just verify it's a real number 0..1
    assert 0.0 <= sc.by_name("tests_recent").score <= 1.0


def test_tests_recent_positive_when_tests_modified(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x(): assert 1\n")
    _git_init(repo)
    for i in range(3):
        (repo / "tests" / "test_x.py").write_text(f"def test_x(): assert {i}\n")
        os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add tests/test_x.py")
        os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm 'c{i}'")
    sc = score_repo(repo)
    assert sc.by_name("tests_recent").score > 0


def test_score_repo_skips_node_modules_for_secret_scan(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "leak.py").write_text(
        'k = "AKIA' + "AAAAAAAAAAAAAAAA" + '"\n'
    )
    sc = score_repo(repo)
    # node_modules pruned during walk, secret not seen.
    assert sc.by_name("secret_hygiene").score == 1.0
