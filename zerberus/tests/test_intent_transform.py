"""
Patch 106 – TRANSFORM-Intent-Detection.
Unit-Test ohne Server-Abhängigkeit (Regex-only).
"""
import pytest

from zerberus.app.routers.orchestrator import detect_intent


@pytest.mark.parametrize("text", [
    "Übersetze folgenden Text auf Englisch: Ich gehe einkaufen.",
    "Übersetz: Hallo",
    "Uebersetze ins Englische: Der Hund liegt auf der Couch",
    "Lektoriere folgenden Absatz: …",
    "Korrigiere: Das ist ein langer satz der zu lang ist",
    "Fasse zusammen: Alice war im Garten und hat geschrien.",
    "Zusammenfassung: die Geschichte handelt von …",
    "Stichpunkte: Wir haben drei Themen besprochen.",
    "Schreib um: Er ging langsam.",
    "Paraphrasiere: Der Himmel ist blau.",
    "Kürze: Dies ist ein sehr langer Text mit vielen Details.",
    "Kuerze: Dies ist zu lang.",
    "Erweitere: Ein kurzer Satz.",
    "Translate to French: Hello world",
    "Summarize the following: ...",
    "Proofread this paragraph for me ...",
    "Rephrase: He walked slowly",
    "bullet points: item a, item b, item c",
])
def test_detects_transform(text):
    assert detect_intent(text) == "TRANSFORM", text


@pytest.mark.parametrize("text,expected", [
    ("Was passiert in den Rosendornen bei der Perseiden-Nacht?", "QUESTION"),
    ("Was heißt Rosendornen auf Englisch?", "QUESTION"),
    ("Hallo wie gehts", "CONVERSATION"),
    ("Erstelle Datei test.txt", "COMMAND_TOOL"),
    ("Zeig mir die Liste", "COMMAND_SAFE"),
    ("Wir sollten Übersetzen lernen", "CONVERSATION"),
    ("", "CONVERSATION"),
])
def test_non_transform_unchanged(text, expected):
    assert detect_intent(text) == expected


def test_transform_not_triggered_in_middle_of_sentence():
    # "übersetze" nicht am Anfang → nicht TRANSFORM
    assert detect_intent("Heute wollte ich übersetze spielen") != "TRANSFORM"
