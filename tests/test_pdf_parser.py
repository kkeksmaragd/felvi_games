"""Tests for felvi_games.pdf_parser.

All OpenAI calls are mocked – no network required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from felvi_games.models import Feladat
from felvi_games.pdf_parser import (
    _dict_to_feladat,
    _id_prefix_from_source,
    extract_feladatok,
    find_exam_pairs,
    parse_exam,
    pdf_to_text,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path, name: str, text: str) -> Path:
    """Write a minimal text-only PDF that pdftotext can read."""
    import pdftotext  # noqa: F401 – ensure it's importable

    # We cannot easily create a real PDF in tests, so we patch pdf_to_text
    # where needed.  This helper creates a sentinel file for path-based tests.
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 placeholder")
    return p


def _gpt_response(feladatok: list[dict]) -> MagicMock:
    """Build a mock OpenAI completion response carrying *feladatok* as JSON."""
    msg = MagicMock()
    msg.content = json.dumps({"feladatok": feladatok})
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


_SAMPLE_ITEM = {
    "id": "mat_2025_1_a",
    "kerdes": "Mennyi 2 + 2?",
    "helyes_valasz": "4",
    "hint": "Alap összeadás.",
    "magyarazat": "Kettő meg kettő négy.",
    "neh": 1,
    "szint": "9 osztályos",
}


# ---------------------------------------------------------------------------
# pdf_to_text
# ---------------------------------------------------------------------------


class TestPdfToText:
    def test_returns_string(self, tmp_path):
        """pdf_to_text wraps pdftotext.PDF and joins pages."""
        p = tmp_path / "dummy.pdf"
        p.write_bytes(b"%PDF placeholder")
        pages_mock = ["Lap 1 szöveg", "Lap 2 szöveg"]
        with patch("felvi_games.pdf_parser.pdftotext.PDF", return_value=pages_mock):
            result = pdf_to_text(p)
        assert "Lap 1 szöveg" in result
        assert "Lap 2 szöveg" in result

    def test_pages_joined_with_double_newline(self, tmp_path):
        p = tmp_path / "dummy.pdf"
        p.write_bytes(b"%PDF placeholder")
        pages_mock = ["A", "B", "C"]
        with patch("felvi_games.pdf_parser.pdftotext.PDF", return_value=pages_mock):
            result = pdf_to_text(p)
        assert result == "A\n\nB\n\nC"

    def test_empty_pdf_returns_empty_string(self, tmp_path):
        p = tmp_path / "empty.pdf"
        p.write_bytes(b"%PDF placeholder")
        with patch("felvi_games.pdf_parser.pdftotext.PDF", return_value=[]):
            result = pdf_to_text(p)
        assert result == ""


# ---------------------------------------------------------------------------
# _id_prefix_from_source
# ---------------------------------------------------------------------------


class TestIdPrefix:
    def test_matek_prefix(self):
        assert _id_prefix_from_source("M8_2025_1_fl.pdf", "matek") == "mat_2025_1"

    def test_magyar_prefix(self):
        assert _id_prefix_from_source("A8_2024_2_fl.pdf", "magyar") == "mag_2024_2"

    def test_handles_missing_parts_gracefully(self):
        prefix = _id_prefix_from_source("unknown.pdf", "matek")
        assert prefix.startswith("mat_")


# ---------------------------------------------------------------------------
# _dict_to_feladat
# ---------------------------------------------------------------------------


class TestDictToFeladat:
    def test_valid_dict_returns_feladat(self):
        f = _dict_to_feladat({**_SAMPLE_ITEM, "targy": "matek"})
        assert isinstance(f, Feladat)
        assert f.id == "mat_2025_1_a"
        assert f.neh == 1

    def test_missing_field_raises_key_error(self):
        bad = {k: v for k, v in _SAMPLE_ITEM.items() if k != "helyes_valasz"}
        with pytest.raises(KeyError):
            _dict_to_feladat(bad)

    def test_invalid_neh_raises_value_error(self):
        bad = {**_SAMPLE_ITEM, "neh": 5}
        with pytest.raises(ValueError):
            _dict_to_feladat(bad)

    def test_neh_coerced_from_string(self):
        f = _dict_to_feladat({**_SAMPLE_ITEM, "neh": "2", "targy": "matek"})
        assert f.neh == 2

    def test_optional_fields_default_to_empty(self):
        f = _dict_to_feladat(_SAMPLE_ITEM)
        assert f.targy == ""
        assert f.pdf_source == ""


# ---------------------------------------------------------------------------
# extract_feladatok (GPT mocked)
# ---------------------------------------------------------------------------


class TestExtractFeladatok:
    def _run(self, items: list[dict], targy: str = "matek") -> list[Feladat]:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _gpt_response(items)
        with patch("felvi_games.pdf_parser._make_openai_client", return_value=mock_client):
            return extract_feladatok(
                fl_text="Feladatlap szöveg",
                ut_text="Útmutató szöveg",
                targy=targy,
                pdf_source="M8_2025_1_fl.pdf",
            )

    def test_single_valid_item(self):
        result = self._run([_SAMPLE_ITEM])
        assert len(result) == 1
        assert result[0].id == "mat_2025_1_a"

    def test_multiple_valid_items(self):
        items = [
            {**_SAMPLE_ITEM, "id": "mat_2025_1_a"},
            {**_SAMPLE_ITEM, "id": "mat_2025_1_b", "neh": 2},
        ]
        result = self._run(items)
        assert len(result) == 2

    def test_targy_injected_into_feladat(self):
        result = self._run([_SAMPLE_ITEM], targy="matek")
        assert result[0].targy == "matek"

    def test_pdf_source_injected(self):
        result = self._run([_SAMPLE_ITEM])
        assert result[0].pdf_source == "M8_2025_1_fl.pdf"

    def test_invalid_item_skipped_not_raised(self):
        bad = {k: v for k, v in _SAMPLE_ITEM.items() if k != "hint"}
        result = self._run([bad, _SAMPLE_ITEM])
        assert len(result) == 1  # only the valid one survives

    def test_empty_gpt_response_returns_empty_list(self):
        result = self._run([])
        assert result == []

    def test_gpt_called_with_correct_model(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _gpt_response([_SAMPLE_ITEM])
        with patch("felvi_games.pdf_parser._make_openai_client", return_value=mock_client):
            extract_feladatok("fl", "ut", "matek", "src.pdf", model="gpt-test-model")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-test-model"

    def test_text_truncated_to_token_budget(self):
        """fl_text longer than 12K chars is still sent (truncated inside function)."""
        long_text = "x" * 20_000
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _gpt_response([])
        with patch("felvi_games.pdf_parser._make_openai_client", return_value=mock_client):
            extract_feladatok(long_text, "ut", "matek", "src.pdf")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        # The original 20K string is truncated to 12K in the prompt
        assert "x" * 12_001 not in user_msg


# ---------------------------------------------------------------------------
# find_exam_pairs
# ---------------------------------------------------------------------------


class TestFindExamPairs:
    def _setup_exams(self, tmp_path: Path, files: list[str]) -> Path:
        exams_dir = tmp_path / "exams" / "9_evfolyam" / "2025"
        exams_dir.mkdir(parents=True)
        for name in files:
            (exams_dir / name).write_bytes(b"%PDF placeholder")
        return tmp_path / "exams"

    def test_matched_pair_yielded(self, tmp_path):
        exams = self._setup_exams(tmp_path, ["M8_2025_1_fl.pdf", "M8_2025_1_ut.pdf"])
        pairs = list(find_exam_pairs(exams))
        assert len(pairs) == 1
        fl, ut, targy = pairs[0]
        assert fl.name == "M8_2025_1_fl.pdf"
        assert ut.name == "M8_2025_1_ut.pdf"
        assert targy == "matek"

    def test_magyar_pair_recognized(self, tmp_path):
        exams = self._setup_exams(tmp_path, ["A8_2024_1_fl.pdf", "A8_2024_1_ut.pdf"])
        pairs = list(find_exam_pairs(exams))
        assert len(pairs) == 1
        assert pairs[0][2] == "magyar"

    def test_missing_ut_skipped(self, tmp_path):
        exams = self._setup_exams(tmp_path, ["M8_2025_1_fl.pdf"])
        pairs = list(find_exam_pairs(exams))
        assert pairs == []

    def test_ut_only_not_yielded(self, tmp_path):
        exams = self._setup_exams(tmp_path, ["M8_2025_1_ut.pdf"])
        pairs = list(find_exam_pairs(exams))
        assert pairs == []

    def test_multiple_pairs_all_found(self, tmp_path):
        files = [
            "M8_2025_1_fl.pdf", "M8_2025_1_ut.pdf",
            "A8_2025_1_fl.pdf", "A8_2025_1_ut.pdf",
        ]
        exams = self._setup_exams(tmp_path, files)
        pairs = list(find_exam_pairs(exams))
        assert len(pairs) == 2
        subjects = {t for _, _, t in pairs}
        assert subjects == {"matek", "magyar"}

    def test_unknown_prefix_ignored(self, tmp_path):
        exams = self._setup_exams(tmp_path, ["X8_2025_1_fl.pdf", "X8_2025_1_ut.pdf"])
        pairs = list(find_exam_pairs(exams))
        assert pairs == []

    def test_empty_directory_returns_empty(self, tmp_path):
        exams = tmp_path / "exams"
        exams.mkdir()
        assert list(find_exam_pairs(exams)) == []


# ---------------------------------------------------------------------------
# parse_exam (integration – pdftotext + GPT mocked)
# ---------------------------------------------------------------------------


class TestParseExam:
    def test_returns_feladatok(self, tmp_path):
        fl = tmp_path / "M8_2025_1_fl.pdf"
        ut = tmp_path / "M8_2025_1_ut.pdf"
        fl.write_bytes(b"%PDF placeholder")
        ut.write_bytes(b"%PDF placeholder")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _gpt_response([_SAMPLE_ITEM])

        with (
            patch("felvi_games.pdf_parser.pdftotext.PDF", return_value=["Szöveg"]),
            patch("felvi_games.pdf_parser._make_openai_client", return_value=mock_client),
        ):
            result = parse_exam(fl, ut, "matek")

        assert len(result) == 1
        assert result[0].targy == "matek"

    def test_pdf_source_set_to_fl_filename(self, tmp_path):
        fl = tmp_path / "M8_2026_2_fl.pdf"
        ut = tmp_path / "M8_2026_2_ut.pdf"
        fl.write_bytes(b"%PDF placeholder")
        ut.write_bytes(b"%PDF placeholder")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _gpt_response([_SAMPLE_ITEM])

        with (
            patch("felvi_games.pdf_parser.pdftotext.PDF", return_value=["Lap"]),
            patch("felvi_games.pdf_parser._make_openai_client", return_value=mock_client),
        ):
            result = parse_exam(fl, ut, "matek")

        assert result[0].pdf_source == "M8_2026_2_fl.pdf"
