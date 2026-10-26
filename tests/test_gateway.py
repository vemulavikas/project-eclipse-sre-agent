from __future__ import annotations

import unittest
from unittest.mock import patch

from gateway import AIGateway, _ProviderSettings


class GatewayRequestTests(unittest.TestCase):
    def test_request_authenticates_with_selected_api_key(self) -> None:
        settings = _ProviderSettings(
            api_key="test-token",
            base_url="https://provider.example/v1",
            model="test-model",
        )
        with patch("gateway.urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = b'{"choices":[]}'

            result = AIGateway._post_json("nvidia", settings, {"messages": []}, 3)

        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(result, {"choices": []})


if __name__ == "__main__":
    unittest.main()
