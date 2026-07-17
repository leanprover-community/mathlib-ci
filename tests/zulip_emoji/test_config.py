"""Tests for emoji_reconcile.config parsing and validation."""

from __future__ import annotations

import copy

import pytest

from emoji_reconcile.config import ConfigError, parse_config

from conftest import SAMPLE_CONFIG_DATA


def _without_state(name: str) -> dict:
    data = copy.deepcopy(SAMPLE_CONFIG_DATA)
    data["states"] = [s for s in data["states"] if s["name"] != name]
    return data


class TestParseValid:
    def test_basic_fields(self, sample_config) -> None:
        assert sample_config.github_repo == "leanprover-community/mathlib4"
        assert sample_config.zulip_site == "https://leanprover.zulipchat.com"
        assert sample_config.zulip_email.endswith("@leanprover.zulipchat.com")

    def test_channels_split_from_rss_allow(self, sample_config) -> None:
        # rss_allow is pulled out of channels into its own list.
        assert "rss_allow" not in sample_config.channels
        assert sample_config.channel_name("pr_reviews") == "PR reviews"
        assert sample_config.rss_allow == ("mathlib bors notifications",)

    def test_managed_emojis_covers_every_rule(self, sample_config) -> None:
        assert sample_config.managed_emojis == {
            "merge", "closed-pr", "bors", "peace_sign", "writing",
            "yellow", "check", "cross_mark", "hammer", "skip_forward",
        }

    def test_realm_emoji_reaction_request_includes_code(self, sample_config) -> None:
        bors = next(r for r in sample_config.states if r.name == "ready-to-merge")
        assert bors.reaction_request(99) == {
            "message_id": 99, "emoji_name": "bors",
            "emoji_code": "22134", "reaction_type": "realm_emoji",
        }

    def test_unicode_emoji_reaction_request_is_minimal(self, sample_config) -> None:
        writing = next(r for r in sample_config.states if r.name == "awaiting-author")
        assert writing.reaction_request(99) == {"message_id": 99, "emoji_name": "writing"}

    def test_sticky_and_group_none_parsed(self, sample_config) -> None:
        migrated = next(r for r in sample_config.states if r.name == "migrated")
        assert migrated.sticky is True
        assert migrated.group is None

    def test_suppression_parsed(self, sample_config) -> None:
        mm = next(r for r in sample_config.states if r.name == "maintainer-merge")
        assert len(mm.suppress_in) == 1
        assert mm.suppress_in[0].channel == "reviewers"
        assert mm.suppress_in[0].subject_prefix == "maintainer merge"

    def test_merged_title_prefix_parsed(self, sample_config) -> None:
        assert sample_config.merged_title_prefix == "[Merged by Bors] -"

    def test_merged_title_prefix_defaults_empty(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data.pop("merged_title_prefix", None)
        assert parse_config(data).merged_title_prefix == ""


class TestParseErrors:
    def test_missing_github_repo(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        del data["github_repo"]
        with pytest.raises(ConfigError, match="github_repo"):
            parse_config(data)

    def test_missing_zulip_email(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        del data["zulip"]["email"]
        with pytest.raises(ConfigError, match="email"):
            parse_config(data)

    def test_empty_states(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"] = []
        with pytest.raises(ConfigError, match="non-empty"):
            parse_config(data)

    def test_source_must_have_exactly_one_kind(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"][0]["source"] = {"state": "merged", "label": "x"}
        with pytest.raises(ConfigError, match="exactly one"):
            parse_config(data)

    def test_source_missing_entirely(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"][0]["source"] = {}
        with pytest.raises(ConfigError, match="exactly one"):
            parse_config(data)

    def test_invalid_state_value(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"][0]["source"] = {"state": "frozen"}
        with pytest.raises(ConfigError, match="source.state"):
            parse_config(data)

    def test_invalid_ci_value(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"][5]["source"] = {"ci": "flaky"}
        with pytest.raises(ConfigError, match="source.ci"):
            parse_config(data)

    def test_suppress_in_unknown_channel(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"][-2]["suppress_in"] = {"channel": "nope"}
        with pytest.raises(ConfigError, match="unknown channel"):
            parse_config(data)

    def test_duplicate_state_names(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["states"].append(dict(data["states"][0]))
        with pytest.raises(ConfigError, match="duplicate state names"):
            parse_config(data)

    def test_rss_allow_must_be_a_list(self) -> None:
        # A bare string would otherwise be silently split into characters.
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["channels"]["rss_allow"] = "mathlib bors notifications"
        with pytest.raises(ConfigError, match="rss_allow"):
            parse_config(data)

    def test_emoji_code_without_reaction_type(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        rule = next(s for s in data["states"] if s["name"] == "delegated")
        rule["emoji_code"] = "123"  # missing reaction_type
        with pytest.raises(ConfigError, match="must be set together"):
            parse_config(data)

    def test_reaction_type_without_emoji_code(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        rule = next(s for s in data["states"] if s["name"] == "delegated")
        rule["reaction_type"] = "realm_emoji"  # missing emoji_code
        with pytest.raises(ConfigError, match="must be set together"):
            parse_config(data)

    def test_merged_title_prefix_must_be_a_string(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data["merged_title_prefix"] = ["not", "a", "string"]
        with pytest.raises(ConfigError, match="merged_title_prefix"):
            parse_config(data)
