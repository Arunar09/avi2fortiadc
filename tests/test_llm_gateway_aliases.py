from core.llm_gateway import GatewayConfig, LLMGateway


def _cfg(mode):
    return {"llm_gateway": {"enabled": True, "mode": mode,
                            "endpoint": "http://127.0.0.1:11434"}}


def test_ollama_is_canonical_mode():
    assert GatewayConfig.from_config(_cfg("ollama")).mode == "ollama"


def test_legacy_opsai_mode_maps_to_ollama():
    assert GatewayConfig.from_config(_cfg("opsai")).mode == "ollama"


def test_default_model_is_vendor_neutral():
    assert GatewayConfig.from_config(_cfg("ollama")).model == "llama3"


def test_directly_constructed_legacy_mode_dispatches_to_ollama(monkeypatch):
    gw = LLMGateway(GatewayConfig(mode="opsai", endpoint="http://127.0.0.1:11434"))
    called = {}

    def fake_ollama(prompt):
        called["prompt"] = prompt
        return {"content": "ok", "model": "llama3", "tokens_used": 1}

    monkeypatch.setattr(gw, "_call_ollama", fake_ollama)
    resp = gw.call("hello")
    assert resp.ok and called["prompt"] == "hello"


def test_disabled_when_not_enabled():
    assert GatewayConfig.from_config({}).mode == "disabled"
