from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from _client import CREATED_BY_LABEL, build_client, friendly_error


class BoxdProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            client, settings = build_client(credentials)
            client.machines.list(org=settings.org, labels=dict([CREATED_BY_LABEL]))
        except ValueError as e:
            raise ToolProviderCredentialValidationError(str(e)) from None
        except Exception as e:
            raise ToolProviderCredentialValidationError(str(friendly_error(e))) from None
