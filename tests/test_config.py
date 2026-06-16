from vision_appliance.config import Settings, _parse_label_set, _parse_zones


def test_parse_zones_supports_multiple_named_regions():
    zones = _parse_zones("workbench:0.5;0.4;1;1,entry:0;0.1;0.2;0.9")

    assert [zone.name for zone in zones] == ["workbench", "entry"]
    assert zones[0].contains_point(800, 500, 1280, 720)


def test_parse_label_set_empty_means_all_labels():
    assert _parse_label_set("*") == set()
    assert _parse_label_set("person, backpack,cell phone") == {"person", "backpack", "cell phone"}


def test_settings_paths_are_derived_from_data_dir(tmp_path):
    settings = Settings(data_dir=tmp_path)

    assert settings.db_path == tmp_path / "events.db"
    assert settings.clips_dir == tmp_path / "clips"

