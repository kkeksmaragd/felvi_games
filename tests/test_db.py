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
        repo.save_tts_assets(feladat_matek, tts_kerdes=b"mp3_kerdes")
        result = repo.get(feladat_matek.id)
        assert result.tts_kerdes_path is not None
        assert repo.load_tts_bytes(result.tts_kerdes_path) == b"mp3_kerdes"

    def test_save_and_retrieve_tts_magyarazat(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek, tts_magyarazat=b"mp3_mag")
        result = repo.get(feladat_matek.id)
        assert result.tts_magyarazat_path is not None
        assert repo.load_tts_bytes(result.tts_magyarazat_path) == b"mp3_mag"

    def test_save_both_assets_at_once(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek, tts_kerdes=b"k", tts_magyarazat=b"m")
        result = repo.get(feladat_matek.id)
        assert repo.load_tts_bytes(result.tts_kerdes_path) == b"k"
        assert repo.load_tts_bytes(result.tts_magyarazat_path) == b"m"

    def test_save_tts_does_not_overwrite_other_asset_with_none(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        updated = repo.save_tts_assets(feladat_matek, tts_kerdes=b"k")
        # Only update magyarazat; kerdes path must stay intact
        repo.save_tts_assets(updated, tts_magyarazat=b"m")
        result = repo.get(feladat_matek.id)
        assert result.tts_kerdes_path is not None
        assert result.tts_magyarazat_path is not None
        assert repo.load_tts_bytes(result.tts_kerdes_path) == b"k"
        assert repo.load_tts_bytes(result.tts_magyarazat_path) == b"m"

    def test_save_tts_raises_for_unknown_id(self, repo):
        nonexistent = _make_feladat("nonexistent")
        with pytest.raises(KeyError):
            repo.save_tts_assets(nonexistent, tts_kerdes=b"x")

    def test_missing_tts_returns_feladatok_without_audio(self, repo):
        m01 = _make_feladat("m01")
        repo.upsert(m01)
        repo.upsert(_make_feladat("m02"))
        repo.save_tts_assets(m01, tts_kerdes=b"audio")
        missing = repo.missing_tts()
        assert len(missing) == 1
        assert missing[0].id == "m02"

    def test_missing_tts_empty_when_all_have_audio(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_tts_assets(feladat_matek, tts_kerdes=b"audio")
        assert repo.missing_tts() == []

    def test_missing_tts_filters_by_targy(self, repo, feladat_matek, feladat_magyar):
        repo.upsert(feladat_matek)
        repo.upsert(feladat_magyar)
        missing_matek = repo.missing_tts(targy="matek")
        assert len(missing_matek) == 1
        assert missing_matek[0].id == feladat_matek.id

    def test_upsert_with_assets_persists_them(self, repo):
        f = _make_feladat("m01").with_assets(tts_kerdes_path="subfolder/m01_kerdes.mp3")
        repo.upsert(f)
        result = repo.get("m01")
        assert result.tts_kerdes_path == "subfolder/m01_kerdes.mp3"


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
        updated = feladat_matek.with_assets(tts_kerdes_path="sub/m_test_01_kerdes.mp3")
        assert updated is not feladat_matek

    def test_with_assets_sets_tts_kerdes(self, feladat_matek):
        updated = feladat_matek.with_assets(tts_kerdes_path="sub/k.mp3")
        assert updated.tts_kerdes_path == "sub/k.mp3"

    def test_with_assets_preserves_unset_field(self, feladat_matek):
        updated = feladat_matek.with_assets(tts_kerdes_path="sub/k.mp3")
        assert updated.tts_magyarazat_path is None

    def test_with_assets_does_not_mutate_original(self, feladat_matek):
        feladat_matek.with_assets(tts_kerdes_path="sub/k.mp3")
        assert feladat_matek.tts_kerdes_path is None


# ---------------------------------------------------------------------------
# Felhasznalo & Menet
# ---------------------------------------------------------------------------


class TestFelhasznalo:
    def test_get_or_create_creates_new(self, repo):
        repo.get_or_create_felhasznalo("Bence")
        from sqlalchemy.orm import Session
        from felvi_games.db import FelhasznaloRecord
        with Session(repo._engine) as session:
            record = session.get(FelhasznaloRecord, "Bence")
        assert record is not None
        assert record.nev == "Bence"

    def test_get_or_create_idempotent(self, repo):
        repo.get_or_create_felhasznalo("Anna")
        repo.get_or_create_felhasznalo("Anna")  # second call must not raise
        from sqlalchemy.orm import Session
        from felvi_games.db import FelhasznaloRecord
        with Session(repo._engine) as session:
            count = session.query(FelhasznaloRecord).filter_by(nev="Anna").count()
        assert count == 1


class TestMenet:
    def test_start_menet_returns_positive_id(self, repo):
        repo.get_or_create_felhasznalo("Tomi")
        menet_id = repo.start_menet("Tomi", "matek", "mind", 10)
        assert isinstance(menet_id, int)
        assert menet_id > 0

    def test_end_menet_sets_ended_at(self, repo):
        from sqlalchemy.orm import Session
        from felvi_games.db import MenetRecord
        repo.get_or_create_felhasznalo("Eva")
        mid = repo.start_menet("Eva", "matek", "mind", 10)
        repo.end_menet(mid)
        with Session(repo._engine) as session:
            record = session.get(MenetRecord, mid)
        assert record.ended_at is not None

    def test_end_menet_idempotent(self, repo):
        repo.get_or_create_felhasznalo("Peti")
        mid = repo.start_menet("Peti", "matek", "mind", 5)
        repo.end_menet(mid)
        repo.end_menet(mid)  # second call must not raise or change ended_at

    def test_update_menet_progress(self, repo):
        from sqlalchemy.orm import Session
        from felvi_games.db import MenetRecord
        repo.get_or_create_felhasznalo("Sara")
        mid = repo.start_menet("Sara", "matek", "6 osztályos", 10)
        repo.update_menet_progress(mid, megoldott=3, pont=27)
        with Session(repo._engine) as session:
            record = session.get(MenetRecord, mid)
        assert record.megoldott == 3
        assert record.pont == 27

    def test_get_menetek_returns_newest_first(self, repo):
        repo.get_or_create_felhasznalo("Nora")
        id1 = repo.start_menet("Nora", "matek", "mind", 10)
        id2 = repo.start_menet("Nora", "magyar", "mind", 5)
        menetek = repo.get_menetek("Nora")
        assert len(menetek) == 2
        assert menetek[0].id == id2  # newest first
        assert menetek[1].id == id1

    def test_get_menetek_empty_for_unknown_user(self, repo):
        assert repo.get_menetek("ismeretlen") == []

    def test_get_menetek_respects_limit(self, repo):
        repo.get_or_create_felhasznalo("Max")
        for _ in range(15):
            repo.start_menet("Max", "matek", "mind", 10)
        assert len(repo.get_menetek("Max", limit=5)) == 5

    def test_menet_domain_fields(self, repo):
        repo.get_or_create_felhasznalo("Dora")
        mid = repo.start_menet("Dora", "matek", "6 osztályos", 10)
        menetek = repo.get_menetek("Dora")
        assert len(menetek) == 1
        m = menetek[0]
        assert m.id == mid
        assert m.felhasznalo == "Dora"
        assert m.targy == "matek"
        assert m.feladat_limit == 10
        assert m.lezart is False


class TestMegoldasWithTracking:
    def test_save_megoldas_with_all_tracking_fields(self, repo, feladat_matek):
        from sqlalchemy.orm import Session
        from felvi_games.db import MegoldasRecord
        repo.upsert(feladat_matek)
        repo.get_or_create_felhasznalo("Zoli")
        mid = repo.start_menet("Zoli", "matek", "mind", 10)
        repo.save_megoldas(
            feladat_matek, "42", Ertekeles(True, "Helyes!", 9),
            felhasznalo_nev="Zoli",
            menet_id=mid,
            elapsed_sec=12.5,
            segitseg_kert=True,
            hibajelezes=False,
        )
        with Session(repo._engine) as session:
            record = session.query(MegoldasRecord).first()
        assert record.felhasznalo_nev == "Zoli"
        assert record.menet_id == mid
        assert record.elapsed_sec == 12.5
        assert record.segitseg_kert is True
        assert record.hibajelezes is False

    def test_save_megoldas_defaults_backward_compatible(self, repo, feladat_matek):
        """Existing call sites without new kwargs must still work."""
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "42", Ertekeles(True, "OK", 9))
        stats = repo.stats()
        assert stats["total_attempts"] == 1


# ---------------------------------------------------------------------------
# FeladatCsoport CRUD
# ---------------------------------------------------------------------------


from felvi_games.models import FeladatCsoport


def _make_csoport(
    id: str = "cg_01",
    targy: str = "matek",
    feladat_sorszam: str = "3",
    max_pont_ossz: int = 3,
) -> FeladatCsoport:
    return FeladatCsoport(
        id=id,
        targy=targy,
        szint="6 osztályos",
        feladat_sorszam=feladat_sorszam,
        ev=2025,
        valtozat=1,
        kontextus="Közös bevezető szöveg.",
        abra_van=False,
        feladat_oldal=5,
        fl_pdf_path=None,
        ut_pdf_path=None,
        fl_szoveg_path=None,
        ut_szoveg_path=None,
        sorrend_kotelezo=False,
        max_pont_ossz=max_pont_ossz,
    )


class TestFeladatCsoport:
    def test_upsert_csoport_and_get(self, repo):
        c = _make_csoport()
        repo.upsert_csoport(c)
        result = repo.get_csoport(c.id)
        assert result is not None
        assert result.id == c.id
        assert result.feladat_sorszam == "3"
        assert result.max_pont_ossz == 3

    def test_upsert_csoport_update(self, repo):
        c = _make_csoport(max_pont_ossz=3)
        repo.upsert_csoport(c)
        import dataclasses
        c2 = dataclasses.replace(c, max_pont_ossz=5)
        repo.upsert_csoport(c2)
        result = repo.get_csoport(c.id)
        assert result.max_pont_ossz == 5

    def test_get_csoport_missing_returns_none(self, repo):
        assert repo.get_csoport("no_such_id") is None

    def test_upsert_many_csoportok(self, repo):
        csoportok = [_make_csoport(id=f"cg_{i:02}", feladat_sorszam=str(i)) for i in range(3)]
        repo.upsert_many_csoportok(csoportok)
        for c in csoportok:
            assert repo.get_csoport(c.id) is not None

    def test_get_feladatok_by_csoport_order(self, repo):
        """Feladatok are returned ordered by csoport_sorrend."""
        c = _make_csoport()
        repo.upsert_csoport(c)
        # Insert feladatok in reverse sorrend order
        for sorrend in (3, 1, 2):
            f = Feladat.from_dict(
                {
                    "id": f"f_{sorrend}",
                    "neh": 1,
                    "szint": "6 osztályos",
                    "kerdes": f"Kérdés {sorrend}",
                    "helyes_valasz": "X",
                    "hint": "H",
                    "magyarazat": "M",
                    "csoport_id": c.id,
                    "csoport_sorrend": sorrend,
                },
                targy="matek",
            )
            repo.upsert(f)
        results = repo.get_feladatok_by_csoport(c.id)
        assert [r.csoport_sorrend for r in results] == [1, 2, 3]

    def test_get_feladatok_by_csoport_empty(self, repo):
        assert repo.get_feladatok_by_csoport("no_csoport") == []


# ---------------------------------------------------------------------------
# New Feladat fields: elfogadott_valaszok, feladat_tipus, max_pont
# ---------------------------------------------------------------------------


class TestNewFeladatFields:
    def test_elfogadott_valaszok_roundtrip(self, repo):
        f = Feladat.from_dict(
            {
                "id": "f_ev",
                "neh": 1,
                "szint": "6 osztályos",
                "kerdes": "Mennyi 2/3 tizedes alakja?",
                "helyes_valasz": "0,667",
                "hint": "Osszuk el",
                "magyarazat": "2 osztva 3-mal",
                "elfogadott_valaszok": ["0,667", "0.667", "2/3"],
                "max_pont": 2,
                "feladat_tipus": "nyilt_valasz",
            },
            targy="matek",
        )
        repo.upsert(f)
        r = repo.get("f_ev")
        assert r.elfogadott_valaszok == ["0,667", "0.667", "2/3"]
        assert r.max_pont == 2
        assert r.feladat_tipus == "nyilt_valasz"

    def test_null_list_fields_roundtrip(self, repo):
        f = _make_feladat("f_null")
        repo.upsert(f)
        r = repo.get("f_null")
        assert r.elfogadott_valaszok is None
        assert r.valaszlehetosegek is None
        assert r.reszpontozas is None
        assert r.ertekeles_megjegyzes is None


# ---------------------------------------------------------------------------
# get_wrong_feladatok
# ---------------------------------------------------------------------------


class TestGetWrongFeladatok:
    def _save(self, repo, feladat, valasz: str, helyes: bool) -> None:
        repo.save_megoldas(feladat, valasz, Ertekeles(helyes, "ok" if helyes else "nem", 1 if helyes else 0))

    def test_empty_returns_empty(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        assert repo.get_wrong_feladatok() == []

    def test_only_correct_answers_not_returned(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        self._save(repo, feladat_matek, "42", True)
        assert repo.get_wrong_feladatok() == []

    def test_wrong_answer_appears(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        self._save(repo, feladat_matek, "99", False)
        rows = repo.get_wrong_feladatok()
        assert len(rows) == 1
        assert rows[0].feladat_id == feladat_matek.id
        assert rows[0].hibas_db == 1
        assert rows[0].osszes_db == 1

    def test_rontas_pct_correct(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        self._save(repo, feladat_matek, "42", True)
        self._save(repo, feladat_matek, "99", False)
        rows = repo.get_wrong_feladatok()
        assert rows[0].osszes_db == 1   # only the wrong attempt is joined
        assert rows[0].rontas_pct == 100.0

    def test_ordered_by_hibas_desc(self, repo):
        f1 = _make_feladat("m01")
        f2 = _make_feladat("m02")
        repo.upsert_many([f1, f2])
        self._save(repo, f1, "bad", False)
        self._save(repo, f2, "bad", False)
        self._save(repo, f2, "worse", False)
        rows = repo.get_wrong_feladatok()
        assert rows[0].feladat_id == "m02"   # 2 wrong answers → first
        assert rows[1].feladat_id == "m01"

    def test_min_hibas_filter(self, repo):
        f1 = _make_feladat("m01")
        f2 = _make_feladat("m02")
        repo.upsert_many([f1, f2])
        self._save(repo, f1, "bad", False)
        self._save(repo, f2, "bad", False)
        self._save(repo, f2, "worse", False)
        rows = repo.get_wrong_feladatok(min_hibas=2)
        assert len(rows) == 1
        assert rows[0].feladat_id == "m02"

    def test_targy_filter(self, repo, feladat_matek, feladat_magyar):
        repo.upsert(feladat_matek)
        repo.upsert(feladat_magyar)
        self._save(repo, feladat_matek, "0", False)
        self._save(repo, feladat_magyar, "valami", False)
        rows = repo.get_wrong_feladatok(targy="matek")
        assert all(r.targy == "matek" for r in rows)
        assert len(rows) == 1

    def test_user_filter(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_megoldas(feladat_matek, "bad", Ertekeles(False, "nem", 0), felhasznalo_nev="Alice")
        repo.save_megoldas(feladat_matek, "bad", Ertekeles(False, "nem", 0), felhasznalo_nev="Bob")
        alice_rows = repo.get_wrong_feladatok(felhasznalo_nev="Alice")
        bob_rows = repo.get_wrong_feladatok(felhasznalo_nev="Bob")
        assert len(alice_rows) == 1
        assert len(bob_rows) == 1
        # Both see same feladat, but counts are per-user
        assert alice_rows[0].hibas_db == 1
        assert bob_rows[0].hibas_db == 1

    def test_limit(self, repo):
        feladatok = [_make_feladat(f"m{i:02}") for i in range(5)]
        repo.upsert_many(feladatok)
        for f in feladatok:
            self._save(repo, f, "bad", False)
        rows = repo.get_wrong_feladatok(limit=3)
        assert len(rows) == 3

    def test_include_wrong_answers(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        self._save(repo, feladat_matek, "wrong1", False)
        self._save(repo, feladat_matek, "wrong2", False)
        rows = repo.get_wrong_feladatok(include_wrong_answers=True)
        assert sorted(rows[0].hibas_valaszok) == ["wrong1", "wrong2"]

    def test_without_include_wrong_answers_list_is_empty(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        self._save(repo, feladat_matek, "bad", False)
        rows = repo.get_wrong_feladatok(include_wrong_answers=False)
        assert rows[0].hibas_valaszok == []


# ---------------------------------------------------------------------------
# save_review – versioning
# ---------------------------------------------------------------------------


import dataclasses


class TestSaveReview:
    """Tests for FeladatRepository.save_review() versioning logic."""

    def _reviewed(self, feladat: "Feladat", **changes) -> "Feladat":
        """Return a copy of feladat with review_elvegezve=True and optional field changes."""
        return dataclasses.replace(feladat, review_elvegezve=True, **changes)

    def test_no_content_change_returns_same_id(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        reviewed = self._reviewed(feladat_matek)
        updated = repo.save_review(reviewed)
        assert updated.id == feladat_matek.id

    def test_no_content_change_sets_review_flag(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        reviewed = self._reviewed(feladat_matek)
        repo.save_review(reviewed)
        assert repo.get(feladat_matek.id).review_elvegezve is True

    def test_no_content_change_record_stays_aktiv(self, repo, feladat_matek):
        from felvi_games.models import FeladatStatusz
        repo.upsert(feladat_matek)
        repo.save_review(self._reviewed(feladat_matek))
        assert repo.get(feladat_matek.id).statusz == FeladatStatusz.AKTIV

    def test_content_change_creates_new_id(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        reviewed = self._reviewed(feladat_matek, kerdes="Módosított kérdés?")
        updated = repo.save_review(reviewed)
        assert updated.id != feladat_matek.id
        assert updated.id == f"{feladat_matek.id}_v2"

    def test_content_change_archives_old_record(self, repo, feladat_matek):
        from felvi_games.models import FeladatStatusz
        repo.upsert(feladat_matek)
        repo.save_review(self._reviewed(feladat_matek, kerdes="Módosított kérdés?"))
        old = repo.get(feladat_matek.id)
        assert old.statusz == FeladatStatusz.ARCHIVALT

    def test_content_change_new_record_is_aktiv(self, repo, feladat_matek):
        from felvi_games.models import FeladatStatusz
        repo.upsert(feladat_matek)
        updated = repo.save_review(self._reviewed(feladat_matek, kerdes="Módosított kérdés?"))
        assert repo.get(updated.id).statusz == FeladatStatusz.AKTIV

    def test_content_change_sets_elozmeny_feladat_id(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        updated = repo.save_review(self._reviewed(feladat_matek, kerdes="Módosított kérdés?"))
        assert updated.elozmeny_feladat_id == feladat_matek.id

    def test_content_change_increments_verzio(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        updated = repo.save_review(self._reviewed(feladat_matek, kerdes="Módosított kérdés?"))
        assert updated.verzio == 2

    def test_second_version_creates_v3(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        v2 = repo.save_review(self._reviewed(feladat_matek, kerdes="v2 kérdés?"))
        repo.upsert(v2)
        v3 = repo.save_review(self._reviewed(v2, kerdes="v3 kérdés?"))
        assert v3.id == f"{feladat_matek.id}_v3"
        assert v3.verzio == 3

    def test_elfogadott_valaszok_change_creates_new_version(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        reviewed = self._reviewed(feladat_matek, elfogadott_valaszok=["42", "42.0"])
        updated = repo.save_review(reviewed)
        assert updated.id == f"{feladat_matek.id}_v2"

    def test_all_returns_only_aktiv_after_review(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_review(self._reviewed(feladat_matek, kerdes="Módosított?"))
        aktiv = repo.all(targy=feladat_matek.targy)
        ids = [f.id for f in aktiv]
        assert feladat_matek.id not in ids
        assert f"{feladat_matek.id}_v2" in ids

    def test_save_review_unknown_id_raises(self, repo, feladat_matek):
        with pytest.raises(KeyError):
            repo.save_review(self._reviewed(feladat_matek))

    def test_megjegyzes_persisted(self, repo, feladat_matek):
        repo.upsert(feladat_matek)
        repo.save_review(self._reviewed(feladat_matek), megjegyzes="tesztnota")
        assert repo.get(feladat_matek.id).review_megjegyzes == "tesztnota"
