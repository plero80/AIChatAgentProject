"""Trusted domain types that can be restored from published workflow checkpoints."""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.models.message_analysis import MessageAnalysis
from backend.models.request_validation import RequestValidation


def checkpoint_serializer():
    # RequestValidation permits reads of completed, pre-guard-migration checkpoints.
    # JSON definitions cannot add classes to this allowlist.
    types = (MessageAnalysis, RequestValidation)
    return JsonPlusSerializer(
        allowed_msgpack_modules=types,
        allowed_json_modules=[(kind.__module__, kind.__name__) for kind in types],
    )
