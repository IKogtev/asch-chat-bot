import pytest

from agent.glossary import (
    CATEGORY_ABBREVIATION,
    CATEGORY_PRODUCT,
    CATEGORY_TERM,
    GlossaryEntry,
    build_doc_search_query,
    build_glossary_expanded_query,
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
            category=CATEGORY_ABBREVIATION,
        ),
        GlossaryEntry(
            term="ФН",
            definition="финансовый навигатор",
            normalized_terms=("фн",),
            category=CATEGORY_ABBREVIATION,
        ),
    ]

    assert find_terms_in_text("Что по накопительное страхование и ФН?", entries) == [
        ["НСЖ", "накопительное страхование жизни", CATEGORY_ABBREVIATION],
        ["ФН", "финансовый навигатор", CATEGORY_ABBREVIATION],
    ]
    assert find_terms_in_text("Что такое НСЖ?", entries) == [
        ["НСЖ", "накопительное страхование жизни", CATEGORY_ABBREVIATION]
    ]


@pytest.mark.unit
def test_find_terms_in_text_finds_multiword_term() -> None:
    entries = [
        GlossaryEntry(
            term="коробочный продукт",
            definition="типовой продукт без индивидуальной настройки",
            normalized_terms=("коробочный продукт",),
            category=CATEGORY_TERM,
        )
    ]

    assert find_terms_in_text("Нужен коробочный продукт для клиента", entries) == [
        [
            "коробочный продукт",
            "типовой продукт без индивидуальной настройки",
            CATEGORY_TERM,
        ]
    ]


@pytest.mark.unit
def test_find_terms_in_text_does_not_match_inside_words() -> None:
    entries = [
        GlossaryEntry(
            term="ФН",
            definition="финансовый навигатор",
            normalized_terms=("фн",),
            category=CATEGORY_ABBREVIATION,
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
            category=CATEGORY_ABBREVIATION,
        )
    ]

    assert find_terms_in_text("обычный вопрос", entries) == []


@pytest.mark.unit
def test_build_doc_search_query_replaces_product_terms() -> None:
    entries = [
        GlossaryEntry(
            term="ФК",
            definition="Fort Knox",
            normalized_terms=("фк",),
            category=CATEGORY_PRODUCT,
        ),
        GlossaryEntry(
            term="НСЖ",
            definition="накопительное страхование жизни",
            normalized_terms=("нсж",),
            category=CATEGORY_ABBREVIATION,
        ),
    ]

    assert build_doc_search_query("дай документы по ФК", entries) == "дай документы по Fort Knox"
    assert (
        build_doc_search_query("что такое НСЖ?", entries)
        == "что такое НСЖ накопительное страхование жизни?"
    )


@pytest.mark.unit
def test_build_doc_search_query_replaces_product_alias() -> None:
    entries = [
        GlossaryEntry(
            term="ФК",
            definition="Fort Knox",
            normalized_terms=("фк", "форт нокс"),
            category=CATEGORY_PRODUCT,
        )
    ]

    assert build_doc_search_query("презентеры по Форт Нокс", entries) == "презентеры по Fort Knox"


@pytest.mark.unit
def test_build_doc_search_query_skips_term_category() -> None:
    entries = [
        GlossaryEntry(
            term="Фокус",
            definition="материалы в фокусе АСЖ",
            normalized_terms=("фокус",),
            category=CATEGORY_TERM,
        ),
        GlossaryEntry(
            term="ГСС",
            definition="Гарантированная страховая сумма",
            normalized_terms=("гсс",),
            category=CATEGORY_ABBREVIATION,
        ),
    ]

    assert (
        build_doc_search_query("документы по фокусу и ГСС", entries)
        == "документы по фокусу и ГСС Гарантированная страховая сумма"
    )


@pytest.mark.unit
def test_build_doc_search_query_does_not_replace_inside_words() -> None:
    entries = [
        GlossaryEntry(
            term="ФН",
            definition="финансовый навигатор",
            normalized_terms=("фн",),
            category=CATEGORY_ABBREVIATION,
        )
    ]

    assert build_doc_search_query("кафнедра", entries) == "кафнедра"

@pytest.mark.unit
def test_build_glossary_expanded_query_alias_matches_doc_search() -> None:
    entries = [
        GlossaryEntry(
            term="ФК",
            definition="Fort Knox",
            normalized_terms=("фк",),
            category=CATEGORY_PRODUCT,
        )
    ]
    query = "дай документы по ФК"
    assert build_glossary_expanded_query(query, entries) == build_doc_search_query(query, entries)


@pytest.mark.unit
def test_build_doc_search_query_returns_original_when_entries_empty() -> None:
    assert build_doc_search_query("дай документы по ФК", []) == "дай документы по ФК"
