import json
import urllib.request
from unittest.mock import patch, MagicMock

import pytest

from Source.download_and_summarize_podcast import call_gemini_api

@patch('urllib.request.urlopen')
def test_call_gemini_api_uses_secure_header(mock_urlopen):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Test summary"}]
                }
            }
        ]
    }).encode('utf-8')
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    prompt = "Summarize this"
    api_key = "test_api_key_123"

    result = call_gemini_api(prompt, api_key)

    assert result == "Test summary"

    # Verify urlopen was called once
    mock_urlopen.assert_called_once()

    # Extract the Request object that was passed to urlopen
    request_arg = mock_urlopen.call_args[0][0]

    # Verify the URL does not contain the API key
    assert request_arg.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    assert "key=" not in request_arg.full_url
    assert api_key not in request_arg.full_url

    # Verify the header contains the API key
    assert request_arg.headers.get("X-goog-api-key") == api_key
    assert request_arg.headers.get("Content-type") == "application/json"
