"""Which Bedrock API serves a model id. Kept free of SDK imports (Lambda cold starts)."""

import re

# Messages-API (Mantle) ids look like "anthropic.claude-opus-5-5". Ids with a
# cross-region prefix ("us.anthropic..."), a version suffix ("...-v1", "...-v1:0")
# or an ARN are Bedrock runtime models, called through the Converse API.
_CONVERSE_ID = re.compile(r"^(arn:|(us|eu|apac|jp|au|global|us-gov)\.)|-v\d+(:\d+)?$")


def uses_converse(model_id: str) -> bool:
    return bool(_CONVERSE_ID.search(model_id))
