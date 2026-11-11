"""Stress marks for synthesis: uv run python -m unittest discover tests"""

import tempfile
import unittest

import stress


class Stress(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# comment\nперезапущУ\nзапущУ  # also\nплохо\nдвОйнОе\n")
        self.words = stress.load(f.name)

    def test_load(self):
        # Exactly one capital letter: the stressed vowel; other lines are skipped.
        self.assertEqual(self.words, {"перезапущу": 9, "запущу": 5})

    def test_stress(self):
        a = stress.ACUTE
        self.assertEqual(stress.stress("Сейчас перезапущу сервис.", self.words), f"Сейчас перезапущу{a} сервис.")
        self.assertEqual(stress.stress("Перезапущу, потом запущу.", self.words), f"Перезапущу{a}, потом запущу{a}.")
        self.assertEqual(stress.stress("перезапущу" + a, self.words), "перезапущу" + a)  # already marked
        self.assertEqual(stress.stress("Перезапущенный сервис.", self.words), "Перезапущенный сервис.")
        self.assertEqual(stress.stress("Текст.", {}), "Текст.")
