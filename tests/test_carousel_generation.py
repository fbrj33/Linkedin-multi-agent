from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from database.models import Post, SessionLocal
from agents import content_agent


def test_carousel_generates_and_persists_all_slides(monkeypatch, tmp_path, test_db):
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setenv("WIMBEE_CAROUSEL_SLIDE_COUNT", "3")
    monkeypatch.chdir(tmp_path)

    class FakeInferenceClient:
        calls = []

        def __init__(self, **kwargs):
            assert kwargs["token"] == "test-token"
            assert kwargs["model"] == "black-forest-labs/FLUX.1-schnell"
            assert kwargs["provider"] == "fal-ai"

        def text_to_image(self, prompt, **kwargs):
            self.calls.append(prompt)
            return Image.new("RGB", (32, 32), color="white")

    monkeypatch.setattr("huggingface_hub.InferenceClient", FakeInferenceClient)

    db = SessionLocal()
    post = Post(format="carrousel", content="Hook\nContext\nAction", status="draft")
    db.add(post)
    db.commit()
    db.refresh(post)
    post_id = post.id
    db.close()

    slides = content_agent.generate_carousel_for_post(post_id)

    assert len(slides) == 3
    assert all(slide["content"] and slide["image_prompt"] for slide in slides)
    assert all(Path(slide["image_path"]).is_file() for slide in slides)
    assert len(FakeInferenceClient.calls) == 3

    db = SessionLocal()
    saved = db.query(Post).filter(Post.id == post_id).first()
    stored_slides = json.loads(saved.carousel_json)
    assert saved.image_path == stored_slides[0]["image_path"]
    assert len(stored_slides) == 3
    db.close()
