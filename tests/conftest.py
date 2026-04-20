"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from felvi_games.db import FeladatRepository
from felvi_games.models import Ertekeles, Feladat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """An isolated in-memory-backed repository for each test."""
    return FeladatRepository(db_path=tmp_path / "test.db")


@pytest.fixture
def feladat_matek() -> Feladat:
    return Feladat.from_dict(
        {
            "id": "m_test_01",
            "neh": 2,
            "szint": "6 osztályos",
            "kerdes": "Mennyi 6 × 7?",
            "helyes_valasz": "42",
            "hint": "Gondolj a szorzótáblára.",
            "magyarazat": "6-szor 7 egyenlő 42.",
        },
        targy="matek",
    )


@pytest.fixture
def feladat_magyar() -> Feladat:
    return Feladat.from_dict(
        {
            "id": "ny_test_01",
            "neh": 1,
            "szint": "6 osztályos",
            "kerdes": "Mi a 'fut' szó szófaja?",
            "helyes_valasz": "ige",
            "hint": "Cselekvést fejez ki?",
            "magyarazat": "A 'fut' cselekvést jelölő ige.",
        },
        targy="magyar",
    )


@pytest.fixture
def ertekeles_helyes() -> Ertekeles:
    return Ertekeles(helyes=True, visszajelzes="Szuper, helyes!", pont=9)


@pytest.fixture
def ertekeles_helytelen() -> Ertekeles:
    return Ertekeles(helyes=False, visszajelzes="Próbáld újra!", pont=0)
