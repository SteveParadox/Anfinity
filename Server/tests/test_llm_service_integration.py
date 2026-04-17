import importlib.util
import sys
import types
import unittest
from pathlib import Path


MODULE_NAME = "test_target_llm_service"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "llm_service.py"
MODULE_SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)

fake_httpx = types.ModuleType("httpx")
fake_httpx.Client = object
sys.modules.setdefault("httpx", fake_httpx)

fake_app = types.ModuleType("app")
fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(
    OPENAI_API_KEY=None,
    OPENAI_MODEL="gpt-4o-mini",
    OPENAI_TIMEOUT=30,
    OLLAMA_BASE_URL="http://localhost:11434",
    OLLAMA_MODEL="phi3:mini",
    OLLAMA_TIMEOUT=150,
    LLM_USE_FALLBACK=True,
    LLM_PROVIDER="ollama",
    LLM_TEMPERATURE=0.3,
    LLM_MAX_TOKENS=1000,
)
fake_app.config = fake_config
sys.modules.setdefault("app", fake_app)
sys.modules.setdefault("app.config", fake_config)

llm_module = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_NAME] = llm_module
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(llm_module)

LLMProvider = llm_module.LLMProvider
LLMResponse = llm_module.LLMResponse


class LLMServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._original_singleton = llm_module._llm_service
        self._original_service_cls = llm_module.LLMService

    def tearDown(self):
        llm_module._llm_service = self._original_singleton
        llm_module.LLMService = self._original_service_cls

    def test_get_llm_service_with_overrides_returns_transient_service(self):
        sentinel_singleton = object()
        llm_module._llm_service = sentinel_singleton

        created = {}

        class FakeService:
            def __init__(self, **kwargs):
                created.update(kwargs)

        llm_module.LLMService = FakeService

        service = llm_module.get_llm_service(
            model="phi3:test",
            primary_provider="ollama",
            use_fallback=False,
        )

        self.assertIsInstance(service, FakeService)
        self.assertEqual(created["ollama_model"], "phi3:test")
        self.assertEqual(created["primary_provider"], "ollama")
        self.assertIs(created["use_fallback"], False)
        self.assertIs(llm_module._llm_service, sentinel_singleton)

    def test_generate_answer_force_ollama_overrides_provider_order(self):
        service = object.__new__(self._original_service_cls)
        service.primary_provider = "openai"
        service.use_fallback = True

        calls = []

        def openai_provider(messages, temperature, max_tokens):
            calls.append("openai")
            return LLMResponse("openai", 1, "gpt", LLMProvider.OPENAI)

        def ollama_provider(messages, temperature, max_tokens):
            calls.append("ollama")
            return LLMResponse("ollama", 1, "phi3", LLMProvider.OLLAMA)

        service._sync_providers = {
            "openai": openai_provider,
            "ollama": ollama_provider,
        }

        response = service.generate_answer("hello", ["context"], force_ollama=True)

        self.assertIs(response.provider, LLMProvider.OLLAMA)
        self.assertEqual(calls, ["ollama"])

    def test_async_generate_answer_force_ollama_overrides_provider_order(self):
        service = object.__new__(self._original_service_cls)
        service.primary_provider = "openai"
        service.use_fallback = True

        calls = []

        async def openai_provider(messages, temperature, max_tokens):
            calls.append("openai")
            return LLMResponse("openai", 1, "gpt", LLMProvider.OPENAI)

        async def ollama_provider(messages, temperature, max_tokens):
            calls.append("ollama")
            return LLMResponse("ollama", 1, "phi3", LLMProvider.OLLAMA)

        service._async_providers = {
            "openai": openai_provider,
            "ollama": ollama_provider,
        }

        async def run_test():
            response = await service.async_generate_answer("hello", ["context"], force_ollama=True)
            self.assertIs(response.provider, LLMProvider.OLLAMA)

        import asyncio

        asyncio.run(run_test())
        self.assertEqual(calls, ["ollama"])


if __name__ == "__main__":
    unittest.main()
