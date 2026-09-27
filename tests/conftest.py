"""
Shared test setup: a headless pygame (SDL's dummy video/audio drivers), the
project root on sys.path, and a throw-away pianists folder so tests never
read or change your own saved pianists.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402


def isolate_pianists(folder):
    """Point pianist.py at `folder` (and forget the cached active pianist)."""
    import pianist
    pianist.FOLDER = folder
    pianist.SETTINGS = os.path.join(folder, "settings.json")
    pianist._active = None


@pytest.fixture(autouse=True)
def _pianists_tmp(tmp_path):
    isolate_pianists(str(tmp_path / "pianists"))
    yield


@pytest.fixture
def screen():
    import pygame
    pygame.init()
    surf = pygame.display.set_mode((1400, 860))
    yield surf
    pygame.quit()


def midi_path(name):
    return os.path.join(ROOT, "MIDIs", name)
