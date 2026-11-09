"""A site's country for the overworld: custom field iso_country first, then the site's region (two-letter slug or
name, or a known country name), then the last line of its physical address; anything else is unknown."""
import pytest

from rackwalker_proxy import netbox as nb
from rackwalker_proxy.country import code_of, site_country
from rackwalker_proxy.scene import site


@pytest.mark.parametrize("cf,slug,name,addr,want", [
    ("NL", "gb", "United Kingdom", "", "NL"),                       # the custom field wins
    (None, "gb", "Somewhere", "", "GB"),                             # two-letter region slug
    (None, "europe-west", "Netherlands", "", "NL"),                  # region named after a country
    (None, None, None, "1 Canal St\nUtrecht, Netherlands", "NL"),    # the address's last line
    (None, None, None, "Unit 4, Leeds, UK", "GB"),
    (None, "eu", "Europe", "", None),                                # two letters but not a country
    ("", "north", "North", "Somewhere far", None),
    ("Netherlands", None, None, "", None),                           # the custom field takes codes only
    (None, None, None, None, None),
])
def test_site_country_sources_in_order(cf, slug, name, addr, want):
    assert site_country(cf, slug, name, addr) == want


def test_codes_are_upper_case():
    assert code_of(" nl ") == "NL" and code_of("Holland") == "NL" and code_of("XX") is None
    assert code_of("uk") == "GB"  # the common short name, not a two-letter code


def test_the_scene_site_carries_the_country_from_the_region_when_no_custom_field():
    s = nb.Site.model_validate({"id": 1, "slug": "a", "name": "A", "status": {"value": "active", "label": "Active"},
                                "region": {"id": 3, "name": "Nederland", "slug": "nl-region"}, "custom_fields": {},
                                "physical_address": None})
    assert site(s).model_dump(mode="json")["country"] == "NL"
