"""CLI tests for `felvi review` command.

Tests invoke the command via typer's CliRunner so no subprocess is spawned.
External side effects (AI call, DB) are mocked to stay fast and deterministic.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from felvi_games.cli import app
from felvi_games.models import Feladat

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def feladat() -> Feladat:
    return Feladat.from_dict(
        {
            "id": "m_cli_01",
            "neh": 2,
            "szint": "6 osztályos",
            "kerdes": "Mennyi 2 + 2?",
            "helyes_valasz": "4",
            "hint": "Összeadás.",
            "magyarazat": "2 plusz 2 egyenlő 4.",
        },
        targy="matek",
    )


@pytest.fixture
def reviewed_feladat(feladat: Feladat) -> Feladat:
    """Same content — no versioning triggered."""
    return dataclasses.replace(feladat, review_elvegezve=True)


@pytest.fixture
def db_file(tmp_path: Path, feladat: Feladat) -> Path:
    """Create a minimal DB with one feladat inserted."""
    db = tmp_path / "test.db"
    from felvi_games.db import FeladatRepository
    repo = FeladatRepository(db_path=db)
    repo.upsert(feladat)
    return db


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_review_no_id_and_no_wrong_flag_exits_nonzero(db_file: Path) -> None:
    result = runner.invoke(app, ["review", "--db", str(db_file)])
    assert result.exit_code != 0


def test_review_nonexistent_db_exits_nonzero(tmp_path: Path) -> None:
    missing_db = tmp_path / "missing.db"
    result = runner.invoke(app, ["review", "any_id", "--db", str(missing_db)])
    assert result.exit_code != 0


def test_review_unknown_feladat_id_exits_nonzero(db_file: Path) -> None:
    result = runner.invoke(app, ["review", "no_such_id", "--db", str(db_file)])
    assert result.exit_code != 0
    assert "nem található" in result.output


def test_review_wrong_no_attempts_prints_message(db_file: Path) -> None:
    """--wrong with no wrong answers in DB should print a message and exit 0."""
    result = runner.invoke(app, ["review", "--wrong", "--db", str(db_file)])
    assert result.exit_code == 0
    assert "hibásan" in result.output


# ---------------------------------------------------------------------------
# Happy path – dry-run (no AI call, mocked)
# ---------------------------------------------------------------------------


def test_review_dry_run_does_not_save(
    db_file: Path, feladat: Feladat, reviewed_feladat: Feladat
) -> None:
    with (
        patch("felvi_games.review.review_feladat_ai", return_value=reviewed_feladat) as mock_ai,
        patch("felvi_games.db.FeladatRepository.save_review") as mock_save,
    ):
        result = runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file), "--dry-run"],
        )
    assert result.exit_code == 0
    mock_ai.assert_called_once()
    mock_save.assert_not_called()
    assert "dry-run" in result.output


def test_review_dry_run_shows_feladat_id(
    db_file: Path, feladat: Feladat, reviewed_feladat: Feladat
) -> None:
    with patch("felvi_games.review.review_feladat_ai", return_value=reviewed_feladat):
        result = runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file), "--dry-run"],
        )
    assert feladat.id in result.output


# ---------------------------------------------------------------------------
# Happy path – in-place update (no content change)
# ---------------------------------------------------------------------------


def test_review_inplace_calls_save_review(
    db_file: Path, feladat: Feladat, reviewed_feladat: Feladat
) -> None:
    with (
        patch("felvi_games.review.review_feladat_ai", return_value=reviewed_feladat),
        patch(
            "felvi_games.db.FeladatRepository.save_review",
            return_value=reviewed_feladat,
        ) as mock_save,
    ):
        result = runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file)],
        )
    assert result.exit_code == 0
    mock_save.assert_called_once()
    assert "In-place" in result.output


# ---------------------------------------------------------------------------
# Happy path – new version (content changed)
# ---------------------------------------------------------------------------


def test_review_versioned_reports_new_id(
    db_file: Path, feladat: Feladat, reviewed_feladat: Feladat
) -> None:
    new_version = dataclasses.replace(reviewed_feladat, id=f"{feladat.id}_v2", verzio=2)
    with (
        patch("felvi_games.review.review_feladat_ai", return_value=reviewed_feladat),
        patch(
            "felvi_games.db.FeladatRepository.save_review",
            return_value=new_version,
        ),
    ):
        result = runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file)],
        )
    assert result.exit_code == 0
    assert f"{feladat.id}_v2" in result.output


def test_review_diff_shown_for_changed_field(
    db_file: Path, feladat: Feladat
) -> None:
    changed = dataclasses.replace(feladat, kerdes="Javított kérdés?", review_elvegezve=True)
    with (
        patch("felvi_games.review.review_feladat_ai", return_value=changed),
        patch(
            "felvi_games.db.FeladatRepository.save_review",
            return_value=changed,
        ),
    ):
        result = runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file)],
        )
    assert "kerdes" in result.output
    assert "Javított kérdés?" in result.output


# ---------------------------------------------------------------------------
# megjegyzes forwarded to AI
# ---------------------------------------------------------------------------


def test_review_megjegyzes_forwarded_to_ai(
    db_file: Path, feladat: Feladat, reviewed_feladat: Feladat
) -> None:
    with (
        patch("felvi_games.review.review_feladat_ai", return_value=reviewed_feladat) as mock_ai,
        patch(
            "felvi_games.db.FeladatRepository.save_review",
            return_value=reviewed_feladat,
        ),
    ):
        runner.invoke(
            app,
            ["review", feladat.id, "--db", str(db_file), "--megjegyzes", "fontos megjegyzés"],
        )
    _, kwargs = mock_ai.call_args
    megjegyzes_arg = kwargs.get("megjegyzes") or mock_ai.call_args.args[2]
    assert megjegyzes_arg == "fontos megjegyzés"
