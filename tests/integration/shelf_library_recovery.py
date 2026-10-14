"""Real shelf cache corruption, thumbnail fallback and failed-build recovery."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.ui import shelf_library as shelf
from open_painter.ui.library_categories import key

open_painter.register()
report = {}
cache = shelf.directory() / shelf.MANIFEST
for invalid in ([], 5, {"version": "0.15.0", "names": []},
                {"version": "0.15.0", "names": {"bad": []}}):
    cache.write_text(json.dumps(invalid))
    shelf._manifest = None
    assert not shelf.current(), "invalid cache must request a rebuild"
assert shelf.ensure(background=True) and shelf.current()
report["corrupt_manifest_rebuilt"] = True

# Refresh must also repair a damaged .blend whose manifest still looks current.
# Existence/version checks alone cannot tell whether Blender can read its contents.
shelf.library_path().write_bytes(b"interrupted asset-library write")
assert shelf.ensure(background=True, force=True)
report["explicit_refresh_rebuilds_damaged_library"] = True

# Every authored material has a readable description, category and real shipped preview.
names = shelf.manifest()["names"]
with bpy.data.libraries.load(str(shelf.library_path()), link=True) as (available, loaded):
    loaded.images = list(names)
try:
    for image in loaded.images:
        assert image.asset_data.description and image.asset_data.catalog_id, image.name
        width, height = image.preview.image_size
        assert width > 0 and height > 0, image.name
        pixels = np.asarray(image.preview.image_pixels_float)
        assert np.isfinite(pixels).all() and pixels.size == width * height * 4, image.name
    report["catalog_entries_with_metadata_and_preview"] = len(loaded.images)
finally:
    for image in loaded.images:
        bpy.data.images.remove(image)

entry = next(e for e in shelf.builtin_entries() if e[1] == "SMART_RECIPE")
before = {image.as_pointer() for image in bpy.data.images}
with TemporaryDirectory() as temporary:
    root = Path(temporary)
    previews = root / "previews"
    previews.mkdir()
    (previews / (entry[0].replace(" ", "_") + ".png")).write_bytes(b"interrupted image download")
    with patch.object(shelf, "PREVIEWS", previews), patch.object(shelf, "builtin_entries", return_value=[entry]):
        path = shelf.build(root / "shelf")
    with bpy.data.libraries.load(str(path), link=True) as (available, loaded):
        loaded.images = list(available.images)
    image = loaded.images[0]
    assert tuple(image.preview.image_size) == (48, 48)
    assert image.get("open_painter_preview_kind") == "COLOR_SWATCH"
    assert shelf.key_of(type("Asset", (), {"metadata": image.asset_data})()) == key(entry)
    bpy.data.images.remove(image)
assert {image.as_pointer() for image in bpy.data.images} == before, "library build leaked images"
report["unreadable_thumbnail_retains_usable_asset_without_leaks"] = True

with patch.object(shelf, "_process", Mock(poll=Mock(return_value=7), returncode=7)), patch.object(shelf, "current", return_value=False):
    shelf._poll_build()
    assert shelf.error_message() and "Refresh Library" in shelf.error_message()
    assert "Refresh Library" in bpy.context.scene.open_painter.status
assert shelf.ensure(background=True)
assert not shelf.error_message(), "successful recovery must clear the error"
report["failed_background_build_reported_and_recovered"] = True
target = ROOT / ".blender-test/shelf-library-recovery.json"
target.write_text(json.dumps(report, indent=2))
open_painter.unregister()
print("SHELF_LIBRARY_RECOVERY_PASS", json.dumps(report))
