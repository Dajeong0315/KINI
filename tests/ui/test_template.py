"""Render the original page without importing the unrelated AI backend."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[2]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.views = []
        self.root_views = []
        self.links = []
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if "data-view" in values:
            self.views.append(values["data-view"])
        if "data-root-view" in values:
            self.root_views.append(values["data-view"])
        if tag == "button" and "data-view-link" in values:
            self.links.append(values["data-view-link"])
        if tag == "script":
            self.scripts.append(values.get("src"))
        if tag == "link" and values.get("rel") == "stylesheet":
            self.styles.append(values.get("href"))


@pytest.fixture
def client():
    app = Flask("ui_test", template_folder=str(ROOT / "app/templates"), static_folder=str(ROOT / "app/static"))
    app.add_url_rule("/", view_func=lambda: render_template("index.html"))
    return app.test_client()


def test_original_views_and_controls_remain_available(client):
    response = client.get("/")
    assert response.status_code == 200
    page = PageParser()
    page.feed(response.get_data(as_text=True))
    assert set(page.views) == {"home", "recommend", "recipe", "cook", "record", "inventory", "report", "profile"}
    assert page.root_views == ["home", "inventory", "recommend", "report", "profile"]
    assert set(page.links) == set(page.root_views)
    assert len(page.ids) == len(set(page.ids))
    assert {"scenario-dialog", "main-content", "household-settings"} <= set(page.ids)
    assert page.scripts == ["/static/api_client.js", "/static/app.js"]
    assert page.styles == ["/static/style.css", "/static/scenario.css"]


def test_all_scenario_assets_are_served_and_no_external_services_are_embedded(client):
    html = client.get("/").get_data(as_text=True)
    for name in ("scenario_demo.js", "api_client.js", "app.js", "scenario.css", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200
    for forbidden in ("api.openai.com", "supabase.co", "cdn.", "TAVILY_API_KEY"):
        assert forbidden not in html
    assert "우리 가족의 영양사" in html
    assert "scenario_demo.js" not in html
    assert "시금치 두부 달걀국" not in html
