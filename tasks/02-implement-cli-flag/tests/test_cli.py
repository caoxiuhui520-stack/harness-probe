import json
import subprocess
import sys

from wordfreq import top_words


def test_cli_prints_top_words_to_stdout(tmp_path):
    src = tmp_path / "sample.txt"
    src.write_text("apple banana apple cherry apple banana", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "wordfreq.py", str(src), "--top", "2"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[:2] == ["apple 3", "banana 2"]


FIFTEEN_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
    "kilo lima mike november oscar"
).split()


def test_cli_default_shows_ten_words(tmp_path):
    src = tmp_path / "sample.txt"
    # Alphabetic only: the tokenizer splits on non-letters, so digit suffixes
    # would collapse into one token and the test would prove nothing.
    src.write_text(" ".join(FIFTEEN_WORDS), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "wordfreq.py", str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.strip().splitlines()) == 10


def test_cli_json_flag_emits_a_json_object(tmp_path):
    src = tmp_path / "sample.txt"
    src.write_text("red blue red", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "wordfreq.py", str(src), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload == {"red": 2, "blue": 1}


def test_cli_errors_on_missing_file(tmp_path):
    proc = subprocess.run(
        [sys.executable, "wordfreq.py", str(tmp_path / "nope.txt")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "no such file" in (proc.stdout + proc.stderr).lower()


def test_library_still_works():
    assert top_words("a b a", 1) == [("a", 2)]
