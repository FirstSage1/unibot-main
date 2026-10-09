"""Проверки индивидуальных сценариев фотографий товара."""

import pytest
from pydantic import ValidationError

from src.services.card_prompts import CardIdea, CardIdeas, build_card_prompt


def make_ideas() -> CardIdeas:
    """Создать реалистичный ответ анализа для тестов."""
    return CardIdeas(
        product="Матовый чёрный термос, цилиндрическая форма, крышка и логотип",
        ideas=[
            CardIdea(
                title="Утро в офисе",
                scene="Термос стоит рядом с ноутбуком, человек наливает напиток утром.",
            ),
            CardIdea(
                title="Поход",
                scene="Термос закреплён в боковом кармане рюкзака на лесной тропе.",
            ),
            CardIdea(
                title="Подарок",
                scene="Термос передают в аккуратной подарочной упаковке дома.",
            ),
        ],
    )


def test_card_ideas_require_three_distinct_scenes() -> None:
    """Ответ анализа содержит ровно три разные идеи."""
    ideas = make_ideas()

    assert len(ideas.ideas) == 3
    assert len({idea.title for idea in ideas.ideas}) == 3


def test_card_ideas_reject_duplicate_scenes() -> None:
    """Повторы не превращаются в одинаковые кнопки для пользователя."""
    values = make_ideas().model_dump()
    values["ideas"][1]["title"] = values["ideas"][0]["title"]

    with pytest.raises(ValidationError):
        CardIdeas.model_validate(values)


def test_build_card_prompt_preserves_product_identity() -> None:
    """Финальный запрос закрепляет видимые детали исходного товара."""
    prompt = build_card_prompt(make_ideas(), 1)

    assert "Матовый чёрный термос" in prompt
    assert "лесной тропе" in prompt
    assert "Сохрани форму, пропорции, цвет" in prompt
    assert "Без коллажа" in prompt


def test_build_card_prompt_rejects_invalid_index() -> None:
    """Несуществующая кнопка не должна приводить к случайному сценарию."""
    with pytest.raises(ValueError, match="Неизвестная идея"):
        build_card_prompt(make_ideas(), 3)
