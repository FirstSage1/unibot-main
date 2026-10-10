"""Проверки запросов AnyModel без внешних HTTP-вызовов."""

import base64
import json

import httpx
import pytest
from openai import AsyncOpenAI

from src.bot.handlers.card_runtime import error_key
from src.core.exceptions import GenerationError, ImageNoOutputError
from src.providers.ai.base import GenerationType
from src.providers.ai.openai_provider import OpenAIAdapter


@pytest.mark.parametrize("with_reference", [True, False])
async def test_image_request_preserves_prompt_and_reference(
    with_reference: bool,
) -> None:
    """Запрос содержит исходный промпт, референс и явный формат результата."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"created": 0, "data": [{"b64_json": "aW1hZ2U="}]}
        )

    adapter = OpenAIAdapter(api_key="test", base_url="https://anymodel.org/v1")
    await adapter._client.close()
    async with AsyncOpenAI(
        api_key="test",
        base_url="https://anymodel.org/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        adapter._client = client
        result = await adapter.generate(
            "am/gpt-image-2",
            "Создай фото термоса на столе.",
            generation_type=GenerationType.IMAGE_EDIT
            if with_reference
            else GenerationType.IMAGE,
            image_data=b"reference" if with_reference else None,
        )
    payload = json.loads(requests[0].content)
    assert result.content == "data:image/png;base64,aW1hZ2U="
    assert payload["prompt"].endswith("Создай фото термоса на столе.")
    assert payload["output_format"] == "png"
    assert payload["response_format"] == "b64_json"
    if with_reference:
        assert payload["images"] == [
            "data:image/jpeg;base64," + base64.b64encode(b"reference").decode()
        ]
    else:
        assert "images" not in payload
        assert "reference" not in payload["prompt"]


@pytest.mark.parametrize("code", ["image_no_output", "content_policy_violation"])
async def test_no_output_is_specific_and_never_retried(code: str) -> None:
    """Отсутствие фото отличается от отказа; ни одна ошибка не повторяется."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": code,
                    "message": "No image",
                    "type": "invalid_request_error",
                }
            },
        )

    adapter = OpenAIAdapter(api_key="test", base_url="https://anymodel.org/v1")
    await adapter._client.close()
    async with AsyncOpenAI(
        api_key="test",
        base_url="https://anymodel.org/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        adapter._client = client
        with pytest.raises(GenerationError) as caught:
            await adapter.generate(
                "am/gpt-image-2", "Товар", generation_type=GenerationType.IMAGE
            )
    assert len(requests) == 1
    if code == "image_no_output":
        assert isinstance(caught.value, ImageNoOutputError)
        assert error_key(caught.value) == "card_image_no_output"
        assert not caught.value.is_retryable
    else:
        assert not isinstance(caught.value, ImageNoOutputError)
