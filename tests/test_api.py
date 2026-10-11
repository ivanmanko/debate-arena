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
from tests.fakes.share import FakePublisher


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


class TestChat:
    def _finished(self, client, text="Тезис."):
        analysis_id = client.post("/api/analyses", json={"text": text, "rounds": 1}).json()["id"]
        _events(client, analysis_id)
        return analysis_id

    def test_ask_returns_exchange_and_detail_has_chat(self, client):
        analysis_id = self._finished(client)
        assert client.get(f"/api/analyses/{analysis_id}").json()["chat"] == []
        response = client.post(f"/api/analyses/{analysis_id}/chat", json={"text": "Почему?"})
        assert response.status_code == 200
        body = response.json()
        assert body["question"]["role"] == "user" and body["question"]["text"] == "Почему?"
        assert body["reply"]["role"] == "judge" and body["reply"]["text"]
        assert body["reply"]["created_at"]
        chat = client.get(f"/api/analyses/{analysis_id}").json()["chat"]
        assert [m["role"] for m in chat] == ["user", "judge"]

    def test_dialogue_legend_anonymizes_question(self, client):
        created = client.post(
            "/api/analyses", json={"text": "Иван: а\nМария: б\nИван: в", "rounds": 1}
        ).json()
        _events(client, created["id"])
        response = client.post(
            f"/api/analyses/{created['id']}/chat",
            json={"text": "Иван прав?", "legend": created["legend"]},
        )
        assert response.json()["question"]["text"] == "Собеседник А прав?"

    def test_missing_is_404(self, client):
        assert client.post("/api/analyses/404/chat", json={"text": "x"}).status_code == 404

    @pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": "x" * 2001}])
    def test_validation_is_422(self, client, body):
        analysis_id = self._finished(client)
        assert client.post(f"/api/analyses/{analysis_id}/chat", json=body).status_code == 422

    def test_message_limit_is_409(self):
        app = create_app(
            _settings(judge_chat_max_messages=1),
            repo=InMemoryRepository(),
            provider_factory=lambda model: FakeProvider(),
        )
        with TestClient(app) as c:
            analysis_id = self._finished(c)
            assert (
                c.post(f"/api/analyses/{analysis_id}/chat", json={"text": "1"}).status_code == 200
            )
            assert (
                c.post(f"/api/analyses/{analysis_id}/chat", json={"text": "2"}).status_code == 409
            )

    def test_delete_while_judge_answers_is_409(self, client):
        analysis_id = self._finished(client)
        client.app.state.chat._busy.add(analysis_id)  # a reply in flight
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 409
        client.app.state.chat._busy.discard(analysis_id)
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 204

    def test_unconfigured_judge_provider_is_503(self):
        from debate_arena.domain.exceptions import ProviderNotConfigured

        def factory(model: str):
            if model == "mock/rational" and factory.analysed:
                raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set")
            return FakeProvider()

        factory.analysed = False
        app = create_app(_settings(), repo=InMemoryRepository(), provider_factory=factory)
        with TestClient(app) as c:
            analysis_id = self._finished(c)
            factory.analysed = True
            response = c.post(f"/api/analyses/{analysis_id}/chat", json={"text": "?"})
            assert response.status_code == 503

    def test_running_analysis_is_409(self):
        app = create_app(
            _settings(), repo=InMemoryRepository(), provider_factory=lambda model: SlowProvider()
        )
        with TestClient(app) as c:
            analysis_id = c.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
            response = c.post(f"/api/analyses/{analysis_id}/chat", json={"text": "?"})
            assert response.status_code == 409
            _events(c, analysis_id)

    def test_provider_failure_is_502_and_nothing_saved(self):
        class FailingChat(FakeProvider):
            async def complete(self, request: ChatRequest) -> Completion:
                if "ОБСУЖДЕНИЕ ВЕРДИКТА" in request.system:
                    raise RuntimeError("upstream 500")
                return await super().complete(request)

        app = create_app(
            _settings(), repo=InMemoryRepository(), provider_factory=lambda model: FailingChat()
        )
        with TestClient(app) as c:
            analysis_id = self._finished(c)
            response = c.post(f"/api/analyses/{analysis_id}/chat", json={"text": "?"})
            assert response.status_code == 502
            assert "upstream 500" in response.json()["detail"]
            assert c.get(f"/api/analyses/{analysis_id}").json()["chat"] == []


class TestShare:
    @pytest.fixture
    def setup(self):
        publisher = FakePublisher()
        app = create_app(
            _settings(),
            repo=InMemoryRepository(),
            provider_factory=lambda model: FakeProvider(),
            publisher=publisher,
        )
        with TestClient(app) as c:
            yield c, publisher

    def _finished(self, client, text="Тезис."):
        analysis_id = client.post("/api/analyses", json={"text": text, "rounds": 1}).json()["id"]
        _events(client, analysis_id)
        return analysis_id

    def test_publish_returns_link_and_detail_shows_it(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        assert client.get(f"/api/analyses/{analysis_id}").json()["share"] is None
        response = client.post(f"/api/analyses/{analysis_id}/share")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"url", "created_at"}
        assert body["url"].startswith("https://snapshots.test/")
        assert client.get(f"/api/analyses/{analysis_id}").json()["share"] == body
        assert len(publisher.objects) == 1

    def test_republish_is_idempotent(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        first = client.post(f"/api/analyses/{analysis_id}/share").json()
        assert client.post(f"/api/analyses/{analysis_id}/share").json() == first
        assert len(publisher.calls) == 1

    def test_missing_is_404(self, setup):
        client, _ = setup
        assert client.post("/api/analyses/999/share").status_code == 404
        assert client.delete("/api/analyses/999/share").status_code == 404

    def test_running_is_409(self):
        app = create_app(
            _settings(),
            repo=InMemoryRepository(),
            provider_factory=lambda model: SlowProvider(),
            publisher=FakePublisher(),
        )
        with TestClient(app) as c:
            analysis_id = c.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
            assert c.post(f"/api/analyses/{analysis_id}/share").status_code == 409
            _events(c, analysis_id)

    def test_storage_failure_is_502(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        publisher.fail = True
        assert client.post(f"/api/analyses/{analysis_id}/share").status_code == 502
        assert client.get(f"/api/analyses/{analysis_id}").json()["share"] is None

    def test_withdraw(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        client.post(f"/api/analyses/{analysis_id}/share")
        assert client.delete(f"/api/analyses/{analysis_id}/share").status_code == 204
        assert publisher.objects == {}
        assert client.get(f"/api/analyses/{analysis_id}").json()["share"] is None
        assert client.delete(f"/api/analyses/{analysis_id}/share").status_code == 404

    def test_withdraw_storage_failure_is_502(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        client.post(f"/api/analyses/{analysis_id}/share")
        publisher.fail = True
        assert client.delete(f"/api/analyses/{analysis_id}/share").status_code == 502
        assert client.get(f"/api/analyses/{analysis_id}").json()["share"] is not None

    def test_deleting_analysis_withdraws_snapshot(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        client.post(f"/api/analyses/{analysis_id}/share")
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 204
        assert publisher.objects == {}

    def test_deleting_analysis_keeps_it_when_storage_fails(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        client.post(f"/api/analyses/{analysis_id}/share")
        publisher.fail = True
        assert client.delete(f"/api/analyses/{analysis_id}").status_code == 502
        assert client.get(f"/api/analyses/{analysis_id}").status_code == 200

    def test_dialogue_snapshot_has_labels_only(self, setup):
        client, publisher = setup
        body = {
            "text": "Иван: вода мокрая\nМария: Иван, нет",
            "question": "Кто прав — Иван или Мария?",
            "rounds": 1,
        }
        analysis_id = client.post("/api/analyses", json=body).json()["id"]
        _events(client, analysis_id)
        assert client.get(f"/api/analyses/{analysis_id}").json()["question"] == (
            "Кто прав — Собеседник А или Собеседник Б?"
        )
        client.post(f"/api/analyses/{analysis_id}/share")
        (page,) = publisher.objects.values()
        assert "Собеседник А" in page
        assert "Иван" not in page and "Мария" not in page

    def test_storage_error_details_stay_on_the_server(self, setup):
        client, publisher = setup
        analysis_id = self._finished(client)
        publisher.fail = True
        detail = client.post(f"/api/analyses/{analysis_id}/share").json()["detail"]
        assert detail == "snapshot storage is unavailable"


class TestShareLocalDir:
    """End to end with the real local storage: the link opens, then stops (SPEC §6 №21)."""

    def test_link_opens_and_stops_after_withdraw(self, tmp_path):
        app = create_app(
            _settings(share_dir=str(tmp_path / "shared")),
            repo=InMemoryRepository(),
            provider_factory=lambda model: FakeProvider(),
        )
        with TestClient(app) as c:
            assert c.get("/shared/nothing.html").status_code == 404
            analysis_id = c.post("/api/analyses", json={"text": "t", "rounds": 1}).json()["id"]
            _events(c, analysis_id)
            url = c.post(f"/api/analyses/{analysis_id}/share").json()["url"]
            assert url.startswith("/shared/")
            page = c.get(url)
            assert page.status_code == 200
            assert page.headers["content-type"].startswith("text/html")
            assert c.delete(f"/api/analyses/{analysis_id}/share").status_code == 204
            assert c.get(url).status_code == 404


class TestConfigAndHealth:
    def test_config_exposes_models_defaults_and_analysts(self, client):
        config = client.get("/api/config").json()
        assert config["mock"] is True
        assert config["default_model"] == "mock/rational"
        assert [m["id"] for m in config["models"]] == ["mock/rational"]
        assert config["default_rounds"] == 2 and config["max_rounds"] == 3
        assert config["max_text_chars"] == 200_000
        assert (config["chat_max_chars"], config["chat_max_messages"]) == (2_000, 20)
        assert config["share_backend"] == "local"
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
