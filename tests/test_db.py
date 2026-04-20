"""Tests for felvi_games.db (FeladatRepository)."""

from __future__ import annotations

import pytest

from felvi_games.db import FeladatRepository
from felvi_games.models import Ertekeles, Feladat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_feladat(id: str = "m01", targy: str = "matek", neh: int = 1) -> Feladat:
    return Feladat.from_dict(
        {
            "id": id,
            "neh": neh,
            "szint": "6 osztályos",
            "kerdes": f"Kérdés {id}",
            "helyes_valasz": "42",
            "hint": "Tipp",
            "magyarazat": "Magyarázat.",
        },
        targy=targy,
    )


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------


class TestInit:
    def test_empty_repo_count_is_zero(self, repo):
        assert repo.count() == 0

    def test_second_repo_instance_reuses_db(self, tmp_path):
        db = tmp_path / "shared.db"
        r1 = FeladatRepository(db_path=db)
        r1.upsert(_make_feladat("x1"))
        r2 = FeladatRepository(db_path=db)
        assert r2.count() == 1


# ---------------------------------------------------------------------------
# Feladat CRUD
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_upsert_inserts_new_record(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        assert repo.count() == 1

    def test_upsert_updates_existing_record(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        updated = Feladat(
            **{**feladat_matek.__dict__, "kerdes": "Módosított kérdés?"}
        )
        repo.upsert(updated)
        assert repo.count() == 1
        assert repo.get(feladat_matek.id).kerdes == "Módosított kérdés?"

    def test_upsert_many_inserts_all(self, repo):
        feladatok = [_make_feladat(f"m{i:02}") for i in range(5)]
        repo.upsert_many(feladatok)
        assert repo.count() == 5

    def test_upsert_many_partial_update(self, repo):
        repo.upsert(_make_feladat("m01"))
        feladatok = [_make_feladat("m01"), _make_feladat("m02")]
        repo.upsert_many(feladatok)
        assert repo.count() == 2


class TestGet:
    def test_get_returns_correct_feladat(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        result = repo.get(feladat_matek.id)
        assert result is not None
        assert result.id == feladat_matek.id
        assert result.kerdes == feladat_matek.kerdes
        assert result.targy == "matek"

    def test_get_returns_none_for_missing_id(self, repo):
        assert repo.get("does_not_exist") is None

    def test_get_preserves_all_fields(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        r = repo.get(feladat_matek.id)
        assert r.neh == feladat_matek.neh
        assert r.szint == feladat_matek.szint
        assert r.helyes_valasz == feladat_matek.helyes_valasz
        assert r.hint == feladat_matek.hint
        assert r.magyarazat == feladat_matek.magyarazat


class TestAll:
    def test_all_returns_every_record(self, repo):
        repo.upsert_many([_make_feladat("m01"), _make_feladat("m02")])
        assert len(repo.all()) == 2

    def test_all_filters_by_targy(self, repo, feladat_matek, feladat_magyar):
        repo.upsert(feladat_matek)
        repo.upsert(feladat_magyar)
        matek = repo.all(targy="matek")
        assert all(f.targy == "matek" for f in matek)
        assert len(matek) == 1

    def test_all_filters_by_szint(self, repo):
        repo.upsert(_make_feladat("m01"))
        f2 = Feladat.from_dict(
            {
                "id": "m02",
                "neh": 2,
                "szint": "8 osztályos",
                "kerdes": "Kérdés",
                "helyes_valasz": "X",
                "hint": "H",
                "magyarazat": "M",
            },
            targy="matek",
        )
        repo.upsert(f2)
        result = repo.all(szint="6 osztályos")
        assert len(result) == 1
        assert result[0].szint == "6 osztályos"

    def test_all_combined_targy_szint_filter(self, repo, feladat_matek, feladat_magyar):
        repo.upsert(feladat_matek)
        repo.upsert(feladat_magyar)
        result = repo.all(targy="magyar", szint="6 osztályos")
        assert len(result) == 1
        assert result[0].id == feladat_magyar.id

    def test_all_empty_db_returns_empty_list(self, repo):
        assert repo.all() == []


# ---------------------------------------------------------------------------
# Asset operations
# ---------------------------------------------------------------------------


class TestTtsAssets:
    def test_save_and_retrieve_tts_kerdes(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek.id, tts_kerdes=b"mp3_kerdes")
        result = repo.get(feladat_matek.id)
        assert result.tts_kerdes == b"mp3_kerdes"

    def test_save_and_retrieve_tts_magyarazat(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek.id, tts_magyarazat=b"mp3_mag")
        result = repo.get(feladat_matek.id)
        assert result.tts_magyarazat == b"mp3_mag"

    def test_save_both_assets_at_once(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek.id, tts_kerdes=b"k", tts_magyarazat=b"m")
        result = repo.get(feladat_matek.id)
        assert result.tts_kerdes == b"k"
        assert result.tts_magyarazat == b"m"

    def test_save_tts_does_not_overwrite_other_asset_with_none(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek.id, tts_kerdes=b"k")
        # Only update magyarazat; kerdes must stay intact
        repo.save_tts_assets(feladat_matek.id, tts_magyarazat=b"m")
        result = repo.get(feladat_matek.id)
        assert result.tts_kerdes == b"k"
        assert result.tts_magyarazat == b"m"

    def test_save_tts_raises_for_unknown_id(self, repo):
        with pytest.raises(KeyError):
            repo.save_tts_assets("nonexistent", tts_kerdes=b"x")

    def test_missing_tts_returns_feladatok_without_audio(self, repo):
        repo.upsert(_make_feladat("m01"))
        repo.upsert(_make_feladat("m02"))
        repo.save_tts_assets("m01", tts_kerdes=b"audio")
        missing = repo.missing_tts()
        assert len(missing) == 1
        assert missing[0].id == "m02"

    def test_missing_tts_empty_when_all_have_audio(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek.id, tts_kerdes=b"audio")
        assert repo.missing_tts() == []

    def test_missing_tts_filters_by_targy(self, repo, feladat_matek, feladat_magyar):
        repo.upsert(feladat_matek)
        repo.upsert(feladat_magyar)
        missing_matek = repo.missing_tts(targy="matek")
        assert len(missing_matek) == 1
        assert missing_matek[0].id == feladat_matek.id

    def test_upsert_with_assets_persists_them(self, repo):
        f = _make_feladat("m01").with_assets(tts_kerdes=b"audio")
        repo.upsert(f)
        result = repo.get("m01")
        assert result.tts_kerdes == b"audio"


# ---------------------------------------------------------------------------
# Megoldas (attempt) tracking
# ---------------------------------------------------------------------------


class TestMegoldas:
    def test_save_megoldas_helyes(self, repo, feladat_matek, ertekeles_helyes):
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "42", ertekeles_helyes)
        stats = repo.stats()
        assert stats["total_attempts"] == 1
        assert stats["correct"] == 1

    def test_save_megoldas_helytelen(self, repo, feladat_matek, ertekeles_helytelen):
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "99", ertekeles_helytelen)
        stats = repo.stats()
        assert stats["total_attempts"] == 1
        assert stats["correct"] == 0

    def test_multiple_attempts_tracked(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "Helyes!", 9))
        repo.save_megoldas(feladat_matek, "99", Ertekeles(False, "Nem jó.", 0))
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "Helyes!", 9))
        stats = repo.stats()
        assert stats["total_attempts"] == 3
        assert stats["correct"] == 2

    def test_stats_accuracy_calculation(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "Helyes!", 10))
        repo.save_megoldas(feladat_matek, "0", Ertekeles(False, "Nem.", 0))
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "Helyes!", 10))
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "Helyes!", 10))
        stats = repo.stats()
        assert stats["accuracy"] == 75.0

    def test_stats_empty_db(self, repo):
        stats = repo.stats()
        assert stats["total_attempts"] == 0
        assert stats["accuracy"] == 0.0

    def test_cascade_delete_removes_megoldasok(self, tmp_path):
        """Deleting a feladat removes its attempts (CASCADE)."""
        from sqlalchemy.orm import Session
        from felvi_games.db import FeladatRecord, MegoldasRecord

        repo = FeladatRepository(db_path=tmp_path / "cascade.db")
        f = _make_feladat("m01")
        repo.upsert(f)
        repo.save_megoldas(f, "42", Ertekeles(True, "OK", 9))

        with Session(repo._engine) as session:
            record = session.get(FeladatRecord, "m01")
            session.delete(record)
            session.commit()

        with Session(repo._engine) as session:
            remaining = session.query(MegoldasRecord).all()
        assert remaining == []


# ---------------------------------------------------------------------------
# Feladat.with_assets (model unit test)
# ---------------------------------------------------------------------------


class TestFeladatWithAssets:
    def test_with_assets_returns_new_instance(self, feladat_matek):
        updated = feladat_matek.with_assets(tts_kerdes=b"audio")
        assert updated is not feladat_matek

    def test_with_assets_sets_tts_kerdes(self, feladat_matek):
        updated = feladat_matek.with_assets(tts_kerdes=b"k")
        assert updated.tts_kerdes == b"k"

    def test_with_assets_preserves_unset_field(self, feladat_matek):
        updated = feladat_matek.with_assets(tts_kerdes=b"k")
        assert updated.tts_magyarazat is None

    def test_with_assets_does_not_mutate_original(self, feladat_matek):
        feladat_matek.with_assets(tts_kerdes=b"k")
        assert feladat_matek.tts_kerdes is None
