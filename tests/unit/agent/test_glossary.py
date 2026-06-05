import pytest

from agent.glossary import GlossaryEntry, find_terms_in_text, normalize_glossary_text


@pytest.mark.unit
def test_normalize_glossary_text_lowercases_and_trims_punctuation() -> None:
    assert normalize_glossary_text("  НСЖ,  ") == "нсж"
    assert normalize_glossary_text("Ёлка   тест") == "елка тест"


@pytest.mark.unit
def test_find_terms_in_text_finds_term_and_alias() -> None:
    entries = [
        GlossaryEntry(
            term="НСЖ",
            definition="накопительное страхование жизни",
            normalized_terms=("нсж", "накопительное страхование"),
        ),
        GlossaryEntry(
            term="ФН",
            definition="финансовый навигатор",
            normalized_terms=("фн",),
        ),
    ]

    assert find_terms_in_text("Что по накопительное страхование и ФН?", entries) == [
        ["НСЖ", "накопительное страхование жизни"],
        ["ФН", "финансовый навигатор"]
    ]
    assert find_terms_in_text("Что такое НСЖ?", entries) == [
        ["НСЖ", "накопительное страхование жизни"]
    ]


@pytest.mark.unit
def test_find_terms_in_text_finds_multiword_term() -> None:
    entries = [
        GlossaryEntry(
            term="коробочный продукт",
            definition="типовой продукт без индивидуальной настройки",
            normalized_terms=("коробочный продукт",),
        )
    ]

    assert find_terms_in_text("Нужен коробочный продукт для клиента", entries) == [
        ["коробочный продукт", "типовой продукт без индивидуальной настройки"]
    ]


@pytest.mark.unit
def test_find_terms_in_text_does_not_match_inside_words() -> None:
    entries = [
        GlossaryEntry(
            term="ФН",
            definition="финансовый навигатор",
            normalized_terms=("фн",),
        )
    ]

    assert find_terms_in_text("кафнедра", entries) == []


@pytest.mark.unit
def test_find_terms_in_text_returns_empty_for_no_matches() -> None:
    entries = [
        GlossaryEntry(
            term="НСЖ",
            definition="накопительное страхование жизни",
            normalized_terms=("нсж",),
        )
    ]

    assert find_terms_in_text("обычный вопрос", entries) == []
