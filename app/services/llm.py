"""
llm.py — Groq LLM integration for answer synthesis.

Takes retrieved evidence and a user question, formats them into a prompt, and
calls the Groq chat-completions API.  The system prompt enforces strict
citation discipline: the model must reference [Evidence N] and never fabricate
information beyond what the evidence provides.

WHY Groq?  It runs open-weight models (Llama, Mixtral) on custom hardware with
sub-second latency, which makes the retrieval-augmented loop feel interactive.
"""

import groq

# The system prompt is long on purpose — it gives the model very explicit
# instructions for citation format, image handling, and honesty about gaps.
SYSTEM_PROMPT = """\
You are an HVAC technical support assistant. Your job is to answer questions
using ONLY the evidence provided below. Follow these rules strictly:

1. Base your answer ONLY on the provided evidence. Do not use prior knowledge.
2. Cite your sources using [Evidence N] notation (e.g., [Evidence 1], [Evidence 3]).
   Every factual claim must have at least one citation.
3. If an evidence item references an image (has an image_path), mention it
   explicitly so the user knows a diagram or figure is available, e.g.:
   "See the wiring diagram in [Evidence 2]."
4. If the provided evidence is insufficient to answer the question, say so
   clearly. Do NOT guess or make up information.
5. Keep answers concise, technically accurate, and well-structured.
   Use bullet points or numbered steps for procedures.
"""


def _format_evidence_block(evidence: list[dict]) -> str:
    """
    Build a numbered context block from the evidence list.

    Each item gets a header with its index, source, and modality so the LLM
    can reference them as [Evidence N] and the user can trace back to the
    original document or recording.
    """
    parts: list[str] = []
    for i, item in enumerate(evidence, start=1):
        header = f"[Evidence {i}] source={item['source']}"
        if item.get("page"):
            header += f", page={item['page']}"
        header += f", modality={item['modality']}"
        if item.get("image_path"):
            header += f", image={item['image_path']}"
        parts.append(f"{header}\n{item.get('text', '')}")
    return "\n\n".join(parts)


class LLMService:
    """
    Thin wrapper around the Groq client for answer synthesis.

    Created once at app startup with the API key and default model.
    """

    def __init__(self, api_key: str, default_model: str) -> None:
        self.client = groq.Groq(api_key=api_key)
        self.default_model = default_model

    def synthesize(
        self,
        question: str,
        evidence: list[dict],
        model: str | None = None,
    ) -> dict:
        """
        Generate a grounded answer from the retrieved evidence.

        Args:
            question: The user's original question.
            evidence: List of evidence dicts (from RetrievalService.search).
            model: Optional model override; falls back to self.default_model.

        Returns:
            Dict with keys: answer, model_used, usage (prompt_tokens,
            completion_tokens).
        """
        model_name = model or self.default_model
        context = _format_evidence_block(evidence)
        user_message = f"Evidence:\n{context}\n\nQuestion: {question}"

        response = self.client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,  # low temp for factual, citation-heavy answers
            max_tokens=1024,
        )

        choice = response.choices[0]
        return {
            "answer": choice.message.content,
            "model_used": model_name,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
        }
