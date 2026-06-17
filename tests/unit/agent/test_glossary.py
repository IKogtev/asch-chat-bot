import pytest

from agent.glossary import (
    GlossaryEntry,
    apply_glossary_to_text,
    find_terms_in_text,
    normalize_glossary_text,
)


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


@pytest.mark.unit
def test_apply_glossary_to_text_replaces_known_terms() -> None:
    glossary = [["ФК", "Fort Knox"], ["НСЖ", "накопительное страхование жизни"]]

    assert apply_glossary_to_text("дай документы по ФК", glossary) == "дай документы по Fort Knox"
    assert apply_glossary_to_text("что такое НСЖ?", glossary) == "что такое накопительное страхование жизни?"


@pytest.mark.unit
def test_apply_glossary_to_text_replaces_longer_terms_first() -> None:
    glossary = [
        ["ФК", "Fort Knox"],
        ["ФК 6", "Fort Knox 6 месяцев"],
    ]

    assert apply_glossary_to_text("презентер ФК 6", glossary) == "презентер Fort Knox 6 месяцев"


@pytest.mark.unit
def test_apply_glossary_to_text_does_not_replace_inside_words() -> None:
    glossary = [["ФН", "финансовый навигатор"]]

    assert apply_glossary_to_text("кафнедра", glossary) == "кафнедра"


@pytest.mark.unit
def test_apply_glossary_to_text_returns_original_when_glossary_empty() -> None:
    assert apply_glossary_to_text("дай документы по ФК", []) == "дай документы по ФК"
