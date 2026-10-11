"""Contract tests for the HTTP API (SPEC §3, §6) — in-memory repo, fake provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from debate_arena.config import Settings
from debate_arena.domain.ports import ChatRequest, Completion, Usage
from debate_arena.main import create_app
from tests.fakes.llm import FakeProvider
from tests.fakes.repo import InMemoryRepository


class SlowProvider(FakeProvider):
    """Keeps the analysis running long enough for a request to observe it."""

    async def complete(self, request: ChatRequest) -> Completion:
        await asyncio.sleep(0.3)
        self.calls.append(request)
        return Completion(text="ok", usage=Usage())


def _settings(**overrides) -> Settings:
    base = dict(anthropic_api_key="", deepseek_api_key="", mock_llm=True, sse_heartbeat_s=0.05)
    return Settings(_env_file=None, **(base | overrides))


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        _settings(), repo=InMemoryRepository(), provider_factory=lambda model: FakeProvider()
    )
    with TestClient(app) as c:
        yield c


def _events(client: TestClient, analysis_id: int) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current: str | None = None
    with client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event:"):
                current = line[6:].strip()
            elif line.startswith("data:") and current:
                events.append((current, json.loads(line[5:])))
                current = None
    return events


class TestCreate:
    def test_accepts_text_and_returns_id(self, client):
        response = client.post("/api/analyses", json={"text": "Тезис.", "rounds": 1})
        assert response.status_code == 202
        assert response.json() == {"id": 1, "legend": {}}

    def test_dialogue_returns_legend_and_stores_placeholders(self, client):
        body = {"text": "Иван: а\nМария: Иван, б", "rounds": 1}
        response = client.post("/api/analyses", json=body)
        assert response.status_code == 202
        assert response.json()["legend"] == {"Собеседник А": "Иван", "Собеседник Б": "Мария"}
        analysis_id = response.json()["id"]
        _events(client, analysis_id)
        detail = client.get(f"/api/analyses/{analysis_id}").json()
        assert detail["participants"] == ["Собеседник А", "Собеседник Б"]
        assert "Иван" not in detail["source_text"]
        assert "legend" not in detail

    @pytest.mark.parametrize(
        "body",
        [
            {"text": ""},
            {"text": "   "},
            {"text": "x", "rounds": 0},
            {"text": "x", "rounds": 99},
            {"text": "x", "model": "openai/gpt"},
            {"text": "x", "model": "anthropic/does-not-exist"},
            {"text": "x", "question": "q" * 501},
            {"text": "x", "mode": "dialogue"},
            {"text": "x", "mode": "weird"},
        ],
    )
    def test_validation_errors(self, client, body):
        assert client.post("/api/analyses", json=body).status_code == 422

    def test_text_over_limit_is_422_with_message(self):
        app = create_app(
            _settings(max_text_chars=10),
            repo=InMemoryRepository(),
            provider_factory=lambda model: FakeProvider(),
        )
        with TestClient(app) as c:
            response = c.post("/api/analyses", json={"text": "x" * 11})
        assert response.status_code == 422
        assert "10" in json.dumps(response.json(), ensure_ascii=False)


class TestDetailAndEvents:
    def test_events_then_detail(self, client):
        analysis_id = client.post("/api/analyses", json={"text": "Тезис.", "rounds": 2}).json()[
            "id"
        ]
        events = _events(client, analysis_id)
        types = [t for t, _ in events]
        assert types[0] == "started" and types[-1] == "done"
        assert types.count("turn") == 6 and "verdict" in types
        assert events[-1][1]["status"] == "finished"

        detail = client.get(f"/api/analyses/{analysis_id}").json()
        assert detail["status"] == "finished"
        assert detail["source_text"] == "Тезис."
        assert detail["mode"] == "text" and detail["participants"] == []
        assert len(detail["turns"]) == 6
        assert detail["verdict"]["closest_to_truth"] is None
        assert detail["verdict"]["participants"] == []
        assert detail["verdict"]["consensus"]
        assert detail["duration_ms"] is not None
        assert {t["round_no"] for t in detail["turns"]} == {1, 2}

    def test_dialogue_detail(self, client):
        body = {"text": "Иван: а\nМария: б\nИван: в", "rounds": 1}
        analysis_id = client.post("/api/analyses", json=body).json()["id"]
        events = _events(client, analysis_id)
        assert events[0][1]["mode"] == "dialogue"
        detail = client.get(f"/api/analyses/{analysis_id}").json()
        assert detail["mode"] == "dialogue"
        assert detail["participants"] == ["Собеседник А", "Собеседник Б"]
        assert detail["verdict"]["closest_to_truth"] in detail["participants"]
        assert {p["name"] for p in detail["verdict"]["participants"]} == set(detail["participants"])
        assert client.get("/api/analyses").json()[0]["mode"] == "dialogue"

    def test_detail_missing_is_404(self, client):
        assert client.get("/api/analyses/404").status_code == 404
        assert client.get("/api/analyses/404/events").status_code == 404

    def test_replay_after_done(self, client):
        analysis_id = client.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
        first = _events(client, analysis_id)
        second = _events(client, analysis_id)
        assert [t for t, _ in first] == [t for t, _ in second]


class TestListAndDelete:
    def test_list_newest_first_and_limit(self, client):
        a = client.post("/api/analyses", json={"text": "один", "rounds": 1}).json()["id"]
        b = client.post("/api/analyses", json={"text": "два", "rounds": 1}).json()["id"]
        _events(client, a)
        _events(client, b)
        items = client.get("/api/analyses").json()
        assert [i["id"] for i in items] == [b, a]
        assert items[0]["title"] == "два"
        assert items[0]["rationality_score"] is not None
        assert len(client.get("/api/analyses?limit=1").json()) == 1

    def test_delete(self, client):
        analysis_id = client.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
        _events(client, analysis_id)
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 204
        assert client.get(f"/api/analyses/{analysis_id}").status_code == 404
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 404

    def test_delete_running_is_409(self):
        app = create_app(
            _settings(), repo=InMemoryRepository(), provider_factory=lambda model: SlowProvider()
        )
        with TestClient(app) as c:
            analysis_id = c.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
            assert c.delete(f"/api/analyses/{analysis_id}").status_code == 409
            _events(c, analysis_id)  # let it finish before the app shuts down


class TestConfigAndHealth:
    def test_config_exposes_models_defaults_and_analysts(self, client):
        config = client.get("/api/config").json()
        assert config["mock"] is True
        assert config["default_model"] == "mock/rational"
        assert [m["id"] for m in config["models"]] == ["mock/rational"]
        assert config["default_rounds"] == 2 and config["max_rounds"] == 3
        assert config["max_text_chars"] == 20_000
        assert len(config["analysts"]) == 3
        assert all(a["emoji"] and a["focus"] for a in config["analysts"])

    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["mock"] is True
        assert body["version"]


class TestFrontend:
    def test_index_and_static_are_served(self, client):
        index = client.get("/")
        assert index.status_code == 200
        assert "Проверка здравого смысла" in index.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
