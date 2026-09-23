"""Prompt versioning (NFR-11) and standing in for a capability a model lacks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from firebid.ai_gateway.config import ModelConfig
from firebid.ai_gateway.emulation import STRUCTURED_TOOL, emulate
from firebid.ai_gateway.errors import CapabilityMissing, ConfigError, OutputInvalid
from firebid.ai_gateway.prompts import load_prompts, prompt_for
from firebid.ai_gateway.types import (
    Capability,
    DocumentPart,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    Message,
    StopReason,
    TextPart,
    ToolCall,
)

VALID = """---
route: demo
purpose: Do the thing.
owner: design-manager
---
Read the drawing and answer.
"""


def write_prompt(root: Path, route: str, name: str, text: str = VALID) -> Path:
    directory = root / route
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    load_prompts.cache_clear()


@pytest.mark.req("NFR-11")
class TestPromptRegistry:
    def test_a_prompt_is_versioned_by_its_content(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "demo", "v1.md")
        first = prompt_for("demo", root=tmp_path)
        assert first is not None
        original = first.version

        load_prompts.cache_clear()
        write_prompt(tmp_path, "demo", "v1.md", VALID.replace("answer.", "answer carefully."))
        edited = prompt_for("demo", root=tmp_path)
        assert edited is not None
        assert edited.version != original, "editing a prompt must change its version"

    def test_the_newest_version_wins(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "demo", "v1.md")
        write_prompt(tmp_path, "demo", "v2.md", VALID.replace("answer.", "answer v2."))
        prompt = prompt_for("demo", root=tmp_path)
        assert prompt is not None
        assert prompt.number == 2

    def test_a_family_variant_is_preferred_when_one_exists(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "demo", "v1.md")
        write_prompt(tmp_path, "demo", "v1.openai.md", VALID.replace("answer.", "answer, openai."))
        assert prompt_for("demo", "openai", root=tmp_path).family == "openai"  # type: ignore[union-attr]
        assert prompt_for("demo", "google", root=tmp_path).family is None  # type: ignore[union-attr]

    def test_a_route_with_no_prompt_is_not_an_error(self, tmp_path: Path) -> None:
        """Many routes are driven entirely by the caller's messages."""
        assert prompt_for("absent", root=tmp_path) is None

    def test_front_matter_is_required(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "demo", "v1.md", "Just some text, no front matter.\n")
        with pytest.raises(ConfigError, match="front matter"):
            prompt_for("demo", root=tmp_path)

    def test_an_anonymous_prompt_is_refused(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "demo", "v1.md", "---\nroute: demo\npurpose: x\n---\nbody\n")
        with pytest.raises(ConfigError, match="owner"):
            prompt_for("demo", root=tmp_path)

    def test_a_prompt_filed_under_the_wrong_route_is_refused(self, tmp_path: Path) -> None:
        write_prompt(tmp_path, "other", "v1.md", VALID)
        with pytest.raises(ConfigError, match="sits in the 'other' directory"):
            prompt_for("other", root=tmp_path)

    def test_the_shipped_prompts_all_parse(self) -> None:
        for route, prompts in load_prompts().items():
            assert prompts, f"{route} has no prompt"
            for prompt in prompts:
                assert prompt.owner and prompt.purpose and prompt.text


class Shape(BaseModel):
    value: int


def request(**changes: object) -> GenerationRequest:
    base: dict[str, object] = {"messages": (Message.user("go"),)}
    base.update(changes)
    return GenerationRequest(**base)  # type: ignore[arg-type]


@pytest.mark.req("NFR-05")
class TestEmulation:
    def test_a_capable_model_is_left_alone(self) -> None:
        model = ModelConfig(
            provider="p",
            model_id="m",
            capabilities=[Capability.STRUCTURED_OUTPUT],
        )
        result = emulate(request(output_model=Shape), model, allowed=True)
        assert result.applied == ()
        assert result.request.output_model is Shape

    def test_without_permission_a_missing_capability_is_refused(self) -> None:
        """A silently degraded answer is worse than a clear refusal."""
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.TOOLS])
        with pytest.raises(CapabilityMissing, match="does not allow emulation"):
            emulate(request(output_model=Shape), model, allowed=False)

    def test_structured_output_becomes_a_tool_call(self) -> None:
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.TOOLS])
        result = emulate(request(output_model=Shape), model, allowed=True)

        assert result.applied == ("structured_output->tool_call",)
        assert result.request.output_model is None
        assert [tool.name for tool in result.request.tools] == [STRUCTURED_TOOL]

        answered = result.finish(
            GenerationResponse(
                text="",
                stop_reason=StopReason.TOOL_CALL,
                provider="p",
                model="m",
                tool_calls=(ToolCall(id="1", name=STRUCTURED_TOOL, arguments={"value": 7}),),
            )
        )
        assert answered.parsed == Shape(value=7)
        assert answered.stop_reason is StopReason.END
        assert answered.emulated == ("structured_output->tool_call",)

    def test_a_tool_call_that_does_not_fit_the_schema_is_rejected(self) -> None:
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.TOOLS])
        result = emulate(request(output_model=Shape), model, allowed=True)
        with pytest.raises(OutputInvalid):
            result.finish(
                GenerationResponse(
                    text="",
                    stop_reason=StopReason.TOOL_CALL,
                    provider="p",
                    model="m",
                    tool_calls=(
                        ToolCall(id="1", name=STRUCTURED_TOOL, arguments={"value": "not a number"}),
                    ),
                )
            )

    def test_a_model_that_never_made_the_call_is_rejected(self) -> None:
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.TOOLS])
        result = emulate(request(output_model=Shape), model, allowed=True)
        with pytest.raises(OutputInvalid, match="did not make one"):
            result.finish(
                GenerationResponse(
                    text="here you go", stop_reason=StopReason.END, provider="p", model="m"
                )
            )

    def test_a_pdf_becomes_images_for_a_vision_only_model(self) -> None:
        pdf = _one_page_pdf()
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.VISION])
        message = Message(
            role="user", parts=(DocumentPart("application/pdf", pdf), TextPart("read it"))
        )

        result = emulate(request(messages=(message,)), model, allowed=True)

        assert result.applied == ("pdf_input->vision",)
        parts = result.request.messages[0].parts
        assert any(isinstance(part, ImagePart) for part in parts)
        assert not any(isinstance(part, DocumentPart) for part in parts)
        # The text alongside the document survives.
        assert any(isinstance(part, TextPart) for part in parts)

    def test_a_model_with_neither_documents_nor_vision_is_refused(self) -> None:
        model = ModelConfig(provider="p", model_id="m", capabilities=[Capability.TOOLS])
        message = Message(role="user", parts=(DocumentPart("application/pdf", b"%PDF-1.4"),))
        with pytest.raises(CapabilityMissing, match="neither documents nor images"):
            emulate(request(messages=(message,)), model, allowed=True)


def _one_page_pdf() -> bytes:
    """A real single-page PDF, so the renderer is genuinely exercised."""
    import pypdfium2

    document = pypdfium2.PdfDocument.new()
    document.new_page(200, 300)
    import io

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
