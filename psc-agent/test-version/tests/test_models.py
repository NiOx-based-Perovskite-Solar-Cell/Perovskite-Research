from psc_agent import cli
from psc_agent.models import DEFAULT_MODEL, MODELS, SUGGESTED, finder_model, role_models, run_model
from psc_agent.web import PROVIDER_MODELS, WebConfig


def test_every_consumer_reads_models_py(tmp_path):
    assert cli.DEFAULT_MODEL is DEFAULT_MODEL
    assert PROVIDER_MODELS["openrouter"] == role_models("openrouter")
    assert WebConfig(root=tmp_path, provider="openrouter").models["decision"] == MODELS["openrouter"]["decision"]
    assert run_model("openrouter") == MODELS["openrouter"]["decision"]
    assert finder_model("openrouter") == MODELS["openrouter"]["si_finder"]


def test_provider_without_finder_and_fallback():
    assert finder_model("gemini") is None
    assert set(role_models("gemini").values()) == {MODELS["gemini"]["default"]}
    assert MODELS["openrouter"]["default"] in SUGGESTED


def test_web_page_suggests_the_models(tmp_path):
    from psc_agent.web import create_app
    c = create_app(WebConfig(root=tmp_path, require_key=False)).test_client()
    html = c.get("/").get_data(as_text=True)
    assert all(f'value="{m}"' in html for m in SUGGESTED)
