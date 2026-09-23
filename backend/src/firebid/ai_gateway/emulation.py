"""Standing in for a capability a model does not have.

A route may allow emulation, in which case the gateway adapts the request rather than refusing
it: a PDF becomes images, structured output becomes a single tool call. What was emulated is
recorded on the call, because an emulated answer is not quite the same thing as a native one
and anyone reading the provenance should know which they have.

Emulation is off unless the route asks for it (`allow_emulation: true`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic import BaseModel, ValidationError

from firebid.ai_gateway.config import ModelConfig
from firebid.ai_gateway.errors import CapabilityMissing, OutputInvalid
from firebid.ai_gateway.types import (
    Capability,
    DocumentPart,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    Message,
    StopReason,
    ToolDef,
)

STRUCTURED_TOOL = "provide_result"


@dataclass(frozen=True)
class Emulated:
    """The adapted request, plus how to finish the response it produces."""

    request: GenerationRequest
    applied: tuple[str, ...]
    structured_via_tool: bool = False

    def finish(self, response: GenerationResponse) -> GenerationResponse:
        """Undo the adaptation on the way back out, so callers see a normal response."""
        if not self.structured_via_tool:
            return _stamp(response, self.applied)

        model = self.request_output_model
        if model is None:
            return _stamp(response, self.applied)

        call = next((call for call in response.tool_calls if call.name == STRUCTURED_TOOL), None)
        if call is None:
            raise OutputInvalid(
                "the model was asked for its answer through a tool call and did not make one"
            )
        try:
            parsed = model.model_validate(dict(call.arguments))
        except ValidationError as error:
            raise OutputInvalid(
                f"tool arguments did not match {model.__name__}: {error}"
            ) from error
        return _stamp(
            replace(
                response,
                parsed=parsed,
                text=response.text or parsed.model_dump_json(),
                stop_reason=StopReason.END,
            ),
            self.applied,
        )

    # Kept separate so `finish` stays readable; set by `emulate`.
    request_output_model: type[BaseModel] | None = None


def _stamp(response: GenerationResponse, applied: tuple[str, ...]) -> GenerationResponse:
    return replace(response, emulated=applied) if applied else response


def emulate(request: GenerationRequest, model: ModelConfig, *, allowed: bool) -> Emulated:
    """Adapt a request to what this model can actually do.

    Raises `CapabilityMissing` when something is needed, absent, and not emulable — better a
    clear refusal than a silently degraded answer.
    """
    capabilities = set(model.capabilities)
    needed = request.requires()
    missing = needed - capabilities
    if not missing:
        return Emulated(request=request, applied=(), request_output_model=None)

    if not allowed:
        raise CapabilityMissing(
            f"model '{model.model_id}' lacks {sorted(str(c) for c in missing)} and this route "
            "does not allow emulation"
        )

    applied: list[str] = []
    adapted = request
    structured_via_tool = False

    if Capability.PDF_INPUT in missing:
        if Capability.VISION not in capabilities:
            raise CapabilityMissing(
                f"model '{model.model_id}' can take neither documents nor images"
            )
        adapted = replace(adapted, messages=_documents_as_images(adapted.messages))
        applied.append("pdf_input->vision")

    if Capability.STRUCTURED_OUTPUT in missing:
        if Capability.TOOLS not in capabilities:
            raise CapabilityMissing(
                f"model '{model.model_id}' has neither structured output nor tools"
            )
        output_model = adapted.output_model
        if output_model is not None:
            tool = ToolDef(
                name=STRUCTURED_TOOL,
                description="Provide the final answer in this exact shape.",
                input_schema=output_model.model_json_schema(),
            )
            adapted = replace(adapted, tools=(*adapted.tools, tool), output_model=None)
            structured_via_tool = True
            applied.append("structured_output->tool_call")

    return Emulated(
        request=adapted,
        applied=tuple(applied),
        structured_via_tool=structured_via_tool,
        request_output_model=request.output_model,
    )


def _documents_as_images(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Render each PDF page to a PNG, so a vision-only model can still read it."""
    import pypdfium2

    rebuilt: list[Message] = []
    for message in messages:
        parts: list[object] = []
        for part in message.parts:
            if isinstance(part, DocumentPart) and part.media_type == "application/pdf":
                document = pypdfium2.PdfDocument(part.data)
                try:
                    for page_number in range(len(document)):
                        page = document[page_number]
                        # 144 dpi: readable for title blocks without exploding the request.
                        image = page.render(scale=2).to_pil()
                        import io

                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG")
                        parts.append(ImagePart("image/png", buffer.getvalue()))
                finally:
                    document.close()
            else:
                parts.append(part)
        rebuilt.append(Message(role=message.role, parts=tuple(parts)))  # type: ignore[arg-type]
    return tuple(rebuilt)
