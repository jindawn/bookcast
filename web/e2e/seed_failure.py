"""Test fixture only: prepare a genuine Core quota failure, not a fake UI response."""
from pathlib import Path
import json
import sys
from unittest.mock import patch
from uuid import uuid4

from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.providers import MockLLMProvider
from bookcast.web_service import Submission, WebService
from bookcast.web_worker import run_worker

root, upload_id = sys.argv[1:]
service = WebService(Path(root))
service.launch = lambda identifier: None
identifier = uuid4().hex
service.submit(Submission(upload_id=upload_id, mode='deep_read', minutes=4), identifier)
original = MockLLMProvider.generate_structured
def fail(self, prompt, response_model):
    data = json.loads(prompt)
    if data['operation'] == 'analysis' and data['chapter']['id'] == '0002':
        raise ProviderError(ErrorKind.QUOTA)
    return original(self, prompt, response_model)
with patch.object(MockLLMProvider, 'generate_structured', fail):
    run_worker(Path(root), identifier)
print(json.dumps(service.status(identifier)))
