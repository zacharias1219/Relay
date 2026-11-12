import json
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def demo_config(tmp_path):
    """The demo (simulated) config with every output path moved into tmp_path."""
    from relay.config import Config

    cfg = {
        "extends": str(ROOT / "config.demo.yaml"),
        "paths": {
            "data_file": str(ROOT / "data/humanevalfix_python.jsonl"),
            "tasks_file": str(ROOT / "data/tasks.json"),
            "traces_dir": str(tmp_path / "traces"),
            "cache_dir": str(tmp_path / "cache"),
            "state_dir": str(tmp_path / "state"),
            "results_json": str(tmp_path / "replay/results.json"),
            "tables_dir": str(tmp_path / "tables"),
            "figures_dir": str(tmp_path / "figures"),
            "annotations": str(tmp_path / "annotations.yaml"),
            "repro_dir": str(tmp_path / "build/repro"),
        },
        "analysis": {"bootstrap_resamples": 500},
    }
    p = tmp_path / "config.test.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return Config.load(p)


@pytest.fixture
def tasks(demo_config):
    from relay.tasks import load_selected

    return load_selected(demo_config)
