"""Tests for compareApi: the class each difference between two API dumps goes into, which
ones are flagged, and what is said when a field is not compared."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

import compareApi
from apiDump import ApiDump, ApiDumpError, read_api
from compareApi import CLASSES, COMPONENTS, Delta, compare, describe_attrs

_REAL = Path(__file__).resolve().parent / "data" / "order-bounds-basic"

# Every field the script reads, each at version 1.
FIELDS = ("n", "k", "m", "d", "a", "p", "x", "g", "dep", "al", "rs", "l",
          "mod", "dm", "md", "imp", "src")


def rec(n: str, **fields) -> dict:
    """A declaration row, as the dumper writes it: a field with its empty value left out."""
    return {"n": n, "k": "thm", "m": "M", **fields}


def dep(to: str | None = None, since: str | None = "2026-01-01", text: str | None = None) -> dict:
    return {"to": to, "since": since, "text": text}


def alias(n: str, of: str, kind: str = "plain", since: str | None = "2026-01-01", **fields) -> dict:
    """A deprecated alias of `of`."""
    return rec(n, dep=dep(of, since), al={"k": kind, "of": of}, **fields)


def dump(rows: list[dict], fields: tuple[str, ...] = FIELDS) -> ApiDump:
    return ApiDump({f: 1 for f in fields},
                   {r["n"]: r for r in rows if "n" in r},
                   {r["mod"]: r for r in rows if "mod" in r})


def cmp(base: list[dict], head: list[dict], *, date: str | None = None,
        cutoff: str | None = None, base_fields: tuple[str, ...] = FIELDS,
        head_fields: tuple[str, ...] = FIELDS) -> Delta:
    day = datetime.date.fromisoformat
    return compare(dump(base, base_fields), dump(head, head_fields),
                   day(date) if date else None, day(cutoff) if cutoff else None)


def names(d: Delta, cls: str) -> list[str]:
    return [it.name for it in d.items.get(cls, [])]


def only(d: Delta, cls: str) -> compareApi.Item:
    """The one item of the one class the delta has."""
    assert {c for c, its in d.items.items() if its} == {cls}
    (it,) = d.items[cls]
    return it


def without(*dropped: str) -> tuple[str, ...]:
    return tuple(f for f in FIELDS if f not in dropped)


class TestTables:
    def test_every_class_has_a_component_that_the_shape_can_name(self) -> None:
        assert {comp for _, comp in CLASSES.values() if comp} == set(COMPONENTS)

    def test_no_change(self) -> None:
        d = cmp([rec("foo", a=["simp"], d="1")], [rec("foo", a=["simp"], d="1", l=7)])
        assert not d.items and d.shape() == "no API change" and not d.warnings()

    def test_the_real_dump_against_itself(self) -> None:
        d = compare(read_api(_REAL / "api.jsonl"), read_api(_REAL / "api.jsonl"))
        assert not d.items and len(d.base) == 412


class TestRemovals:
    def test_a_removed_name_is_flagged_as_removed_or_renamed(self) -> None:
        d = cmp([rec("old"), rec("kept")], [rec("kept")])
        it = only(d, "removed")
        assert it.warn == "removed or renamed without a deprecation"
        assert d.shape() == "removals" and d.warnings() == [("removed", it)]
        assert d.title("removed") == "removed or renamed without deprecation"

    def test_a_rename_is_a_removal_and_an_addition(self) -> None:
        """Statements are not compared, so nothing pairs the two names."""
        d = cmp([rec("old")], [rec("new")])
        assert names(d, "removed") == ["old"] and names(d, "added") == ["new"]
        assert d.shape() == "mixed: removals + additions"

    def test_a_removed_instance_is_listed_and_not_flagged(self) -> None:
        d = cmp([rec("instFoo", a=["instance:1000"]), rec("kept")], [rec("kept")])
        assert only(d, "removed").note == "an instance, removed or renamed" and not d.warnings()

    def test_sub_declarations_go_with_their_parent(self) -> None:
        d = cmp([rec("Foo", k="ind"), rec("Foo.mk", k="ctor"), rec("Foo.rec", k="rec")], [rec("x")])
        assert names(d, "removed") == ["Foo"] and d.folded == {"Foo": ["Foo.mk", "Foo.rec"]}

    def test_a_translation_that_got_the_deprecation_is_named(self) -> None:
        base = [rec("mul_foo"), rec("add_foo", x="mul_foo", g=True)]
        head = [alias("mul_foo", "mul_bar"), rec("mul_bar"), rec("add_bar", x="mul_bar", g=True)]
        d = cmp(base, head)
        (it,) = d.items["removed"]
        assert it.warn == ("removed or renamed without a deprecation (its translation mul_foo "
                           "did get one)")
        assert it.refs == ["mul_foo"]

    def test_a_generated_name_whose_source_lost_the_attribute(self) -> None:
        base = [rec("src"), rec("gen", x="src", g=True)]
        d = cmp(base, [rec("src")])
        assert only(d, "removed").warn == (
            "removed or renamed without a deprecation; it was generated from src, which no "
            "longer has the translation attribute that made it")

    def test_but_not_when_the_source_translates_to_another_name_now(self) -> None:
        base = [rec("src"), rec("gen", x="src", g=True)]
        head = [rec("src"), rec("gen2", x="src", g=True)]
        (it,) = cmp(base, head).items["removed"]
        assert it.warn == "removed or renamed without a deprecation"

    def test_a_removed_deprecated_name_is_not_flagged(self) -> None:
        d = cmp([alias("old", "new"), rec("new")], [rec("new")])
        it = only(d, "dep-removed")
        assert (it.note, it.since) == ("deprecated 2026-01-01 → new", "2026-01-01")
        assert it.refs == ["new"]
        assert not d.warnings() and d.shape() == "deprecation removal"

    def test_unless_it_was_deprecated_after_the_cutoff(self) -> None:
        base = [alias("old", "new", since="2026-03-01"), rec("new")]
        assert not cmp(base, [rec("new")], cutoff="2026-03-01").warnings()
        d = cmp(base, [rec("new")], cutoff="2026-02-28")
        assert only(d, "dep-removed").warn == "deprecated 2026-03-01, after the cutoff 2026-02-28"

    def test_a_name_realised_on_demand_is_noise(self) -> None:
        d = cmp([rec("foo.eq_1", rs=True), rec("foo")], [rec("foo"), rec("foo.eq_2", rs=True)])
        assert [(it.name, it.note) for it in d.items["aux"]] == [("foo.eq_1", "removed"),
                                                                 ("foo.eq_2", "added")]
        assert d.shape() == "no API change" and not d.warnings()


class TestRenamedDeprecations:
    BASE = [alias("old_alias", "target"), rec("target")]

    def test_a_deprecated_alias_renamed_is_paired_by_its_target(self) -> None:
        d = cmp(self.BASE, [alias("new_alias", "target", since="2026-02-02"), rec("target")])
        it = only(d, "dep-renamed")
        assert it.name == "old_alias" and it.refs == ["new_alias", "target"]
        assert it.note == "now new_alias, deprecated 2026-02-02 → target"
        assert it.warn == ("deprecated alias renamed to new_alias, so code using the old name "
                           "now fails instead of warning; keep the old name")

    def test_or_by_where_its_target_now_points(self) -> None:
        head = [alias("new_alias", "target2"), alias("target", "target2"), rec("target2")]
        d = cmp(self.BASE, head)
        assert names(d, "dep-renamed") == ["old_alias"] and names(d, "deprecated") == ["target"]

    def test_the_one_with_the_same_date_is_preferred(self) -> None:
        head = [alias("a_alias", "target", since="2026-05-05"), alias("b_alias", "target"),
                rec("target")]
        d = cmp(self.BASE, head)
        assert d.items["dep-renamed"][0].refs == ["b_alias", "target"]
        assert names(d, "deprecated") == ["a_alias"]

    def test_not_with_a_name_of_another_kind(self) -> None:
        d = cmp(self.BASE, [alias("new_alias", "target", k="def"), rec("target")])
        assert names(d, "dep-removed") == ["old_alias"] and names(d, "deprecated") == ["new_alias"]

    def test_before_the_cutoff_the_new_name_should_go_instead(self) -> None:
        d = cmp(self.BASE, [alias("new_alias", "target"), rec("target")], cutoff="2026-06-01")
        assert only(d, "dep-renamed").warn == (
            "deprecated alias renamed to new_alias; deprecated 2026-01-01, before the cutoff "
            "2026-06-01, so remove new_alias rather than restore the old name")


class TestNewDeprecations:
    def test_an_alias_of_a_new_name(self) -> None:
        d = cmp([rec("old")], [alias("old", "new"), rec("new")])
        it = only(d, "deprecated")
        assert (it.note, it.since, it.refs) == ("alias → new (new name)", "2026-01-01", ["new"])
        assert not d.warnings() and d.shape() == "deprecations"

    def test_the_new_names_translations_and_fields_come_with_it(self) -> None:
        head = [alias("old", "New"), rec("New", k="ind"), rec("New.mk", k="ctor"),
                rec("AddNew", x="New", g=True)]
        assert names(only_classes(cmp([rec("old")], head), "deprecated"), "added") == []

    def test_an_iff_alias_and_an_existing_name(self) -> None:
        base = [rec("old"), rec("iff")]
        d = cmp(base, [alias("old", "iff", kind="forward"), rec("iff")])
        assert only(d, "deprecated").note == "alias → (.mp) iff (existing name)"

    def test_deprecated_without_an_alias_or_a_replacement(self) -> None:
        head = [rec("a", dep=dep("b")), rec("b"),
                rec("c", dep=dep(text="Use the API of `Foo`.")),
                rec("e", dep=dep(text="No replacement."))]
        d = cmp([rec("a"), rec("b"), rec("c"), rec("e")], head)
        assert [it.note for it in d.items["deprecated"]] == [
            "in favour of b (not an alias) (existing name)",
            "no replacement: Use the API of `Foo`.", "no replacement"]

    def test_a_target_that_is_itself_deprecated(self) -> None:
        head = [alias("old", "mid"), alias("mid", "new"), rec("new")]
        d = cmp([rec("old"), alias("mid", "new"), rec("new")], head)
        it = only(d, "deprecated")
        assert it.note == "alias → mid (existing name); mid is itself deprecated → new"
        assert it.refs == ["mid", "new"]

    def test_attributes_not_carried_over(self) -> None:
        d = cmp([rec("old", a=["ext", "simp"])], [alias("old", "new"), rec("new", a=["ext"])])
        assert only(d, "deprecated").warn == "attributes not carried over to new: ['simp']"

    def test_a_docstring_not_carried_over(self) -> None:
        d = cmp([rec("old", d="1")], [alias("old", "new"), rec("new")])
        assert only(d, "deprecated").warn == "docstring not carried over to new"
        assert not cmp([rec("old", d="1")], [alias("old", "new"), rec("new", d="2")]).warnings()

    def test_an_alias_that_drops_protected_is_told_the_line_to_write(self) -> None:
        d = cmp([rec("Foo.old", p=True)], [alias("Foo.old", "Foo.new"), rec("Foo.new", p=True)])
        assert only(d, "deprecated").warn == (
            "no longer `protected`, so `open Foo` now brings `old` into scope; "
            "write `protected alias old := Foo.new`")

    def test_one_deprecated_in_place_is_told_to_stay_protected(self) -> None:
        d = cmp([rec("Foo.old", p=True), rec("Foo.new")],
                [rec("Foo.old", dep=dep("Foo.new")), rec("Foo.new")])
        assert only(d, "deprecated").warn.endswith("; keep it `protected`")

    def test_dropping_protected_adds_to_another_warning(self) -> None:
        d = cmp([rec("Foo.old", p=True, d="1")], [alias("Foo.old", "Foo.new"), rec("Foo.new")])
        assert only(d, "deprecated").warn.startswith(
            "docstring not carried over to Foo.new; also no longer `protected`, so ")

    def test_gaining_protected_is_noted(self) -> None:
        d = cmp([rec("Foo.old")], [alias("Foo.old", "Foo.new", p=True), rec("Foo.new", p=True)])
        it = only(d, "deprecated")
        assert it.note.endswith("; `protected` now, though the old name was not") and not it.warn

    @pytest.mark.parametrize("since, warn, note", [
        (None, "no `since` date", ""),
        ("soon", "malformed `since` date", ""),
        ("2026-06-02", "`since` 2026-06-02 is after the PR's date 2026-06-01", ""),
        ("2026-06-01", "", ""),
        ("2026-04-01", "", "; `since` is 61 days before the PR"),
    ])
    def test_the_since_date(self, since: str | None, warn: str, note: str) -> None:
        d = cmp([rec("old")], [alias("old", "new", since=since), rec("new")], date="2026-06-01")
        it = only(d, "deprecated")
        assert it.warn == warn and it.note == "alias → new (new name)" + note

    def test_a_name_that_arrives_deprecated(self) -> None:
        d = cmp([rec("new")], [rec("new"), alias("restored", "new", since=None)])
        it = only(d, "deprecated")
        assert it.note == "new deprecated alias → new" and it.warn == "no `since` date"

    def test_a_deprecation_withdrawn_is_flagged(self) -> None:
        d = cmp([alias("old", "new"), rec("new")], [rec("old"), rec("new")])
        it = only(d, "undeprecated")
        assert (it.note, it.warn) == ("was deprecated 2026-01-01", "deprecation withdrawn")

    def test_an_older_deprecation_re_pointed(self) -> None:
        base = [alias("older", "old"), rec("old")]
        head = [alias("older", "new"), alias("old", "new"), rec("new")]
        d = cmp(base, head)
        (it,) = d.items["dep-retargeted"]
        assert it.note == "now → new (was → old, which this PR deprecates)" and not it.warn

    def test_a_plain_alias_re_pointed_is_its_own_shape(self) -> None:
        base = [rec("a", al={"k": "plain", "of": "old"}), rec("old"), rec("new")]
        head = [rec("a", al={"k": "plain", "of": "new"}), rec("new")]
        d = cmp(base, head)
        (it,) = d.items["alias-retargeted"]
        assert it.note == "now alias of new (was of old, which this PR removes)"
        assert d.shape() == "mixed: removals + alias changes"


def only_classes(d: Delta, *classes: str) -> Delta:
    assert {c for c, its in d.items.items() if its} == set(classes)
    return d


class TestSurvivingNames:
    def test_attributes(self) -> None:
        d = cmp([rec("foo", a=["ext", "simp"])], [rec("foo", a=["norm_cast", "simp"])])
        assert only(d, "attrs").note == "+norm_cast −ext" and d.shape() == "attribute changes"

    @pytest.mark.parametrize("before, after, words", [
        (["instance:1000"], ["instance:90"], "instance priority 1000 → 90"),
        ([], ["instance:1000"], "now a global instance"),
        ([], ["instance:100"], "now a global instance, priority 100"),
        (["instance:1000"], [], "no longer a global instance"),
        (["reducibility:[reducible]"], [], "reducibility reducible → semireducible"),
        ([], ["defeq", "simp"], "+simp; now used by `dsimp`"),
        (["defeq"], [], "no longer used by `dsimp`"),
    ])
    def test_attributes_in_words(self, before: list, after: list, words: str) -> None:
        assert describe_attrs(before, after) == words

    def test_a_simp_lemma_that_dsimp_stops_using_is_flagged(self) -> None:
        d = cmp([rec("foo", a=["defeq", "simp"])], [rec("foo", a=["simp"])])
        assert only(d, "attrs").warn.startswith("no longer used by `dsimp`, though still `simp`")

    def test_a_translation_with_the_same_change_is_listed_under_its_source(self) -> None:
        base = [rec("mul", a=["instance:1000"]), rec("add", a=["instance:1000"], x="mul", g=True)]
        head = [rec("mul", a=["instance:90"]), rec("add", a=["instance:90"], x="mul", g=True)]
        it = only(cmp(base, head), "attrs")
        assert it.name == "mul" and it.copies == ["add"]

    def test_but_not_one_with_another_change(self) -> None:
        base = [rec("mul", a=["simp"]), rec("add", a=["simp"], x="mul", g=True)]
        head = [rec("mul"), rec("add", a=["ext"], x="mul", g=True)]
        assert names(cmp(base, head), "attrs") == ["add", "mul"]

    def test_lemmas_dsimp_now_uses_are_listed_under_their_definition(self) -> None:
        base = [rec("opMap", k="def"), rec("opMap_τ₁", a=["simp"]), rec("other", a=["simp"])]
        head = [rec("opMap", k="def", a=["reducibility:[implicit_reducible]"]),
                rec("opMap_τ₁", a=["defeq", "simp"]), rec("other", a=["defeq", "simp"])]
        d = cmp(base, head)
        assert names(d, "attrs") == ["opMap", "other"]
        assert d.items["attrs"][0].dsimp == ["opMap_τ₁"]

    def test_a_generated_copy_brings_its_lemmas_now_used_by_dsimp(self) -> None:
        """`down`, `up`'s generated dual, is listed under `up`, and so is `down_Rel`."""
        made = "reducibility:[implicit_reducible]"
        base = [rec("up", k="def"), rec("down", k="def", g=True, x="up"),
                rec("up_Rel", a=["simp"]), rec("down_Rel", a=["simp"], g=True, x="up_Rel")]
        head = [rec("up", k="def", a=[made]), rec("down", k="def", a=[made], g=True, x="up"),
                rec("up_Rel", a=["defeq", "simp"]),
                rec("down_Rel", a=["defeq", "simp"], g=True, x="up_Rel")]
        it = only(cmp(base, head), "attrs")
        assert (it.name, it.copies, it.dsimp) == ("up", ["down"], ["up_Rel", "down_Rel"])

    def test_docstrings(self) -> None:
        base = [rec("a", d="1"), rec("b", d="1"), rec("c")]
        head = [rec("a"), rec("b", d="2"), rec("c", d="3")]
        d = cmp(base, head)
        assert [(it.name, it.note, it.warn) for it in d.items["doc"]] == [
            ("a", "lost", "docstring lost"), ("b", "reworded", ""), ("c", "added", "")]

    def test_protected(self) -> None:
        d = cmp([rec("Foo.a", p=True), rec("Foo.b")], [rec("Foo.a"), rec("Foo.b", p=True)])
        assert [it.note for it in d.items["visibility"]] == ["no longer protected", "now protected"]

    def test_generated_and_hand_written(self) -> None:
        base = [rec("src"), rec("a"), rec("b", x="src", g=True)]
        head = [rec("src"), rec("a", x="src", g=True), rec("b")]
        d = cmp(base, head)
        assert names(d, "now-generated") == ["a"] and names(d, "now-hand-written") == ["b"]
        assert d.shape() == "mixed: now hand-written + now generated"

    def test_a_move(self) -> None:
        d = cmp([rec("foo", m="A")], [rec("foo", m="B")])
        it = only(d, "moved")
        assert (it.note, it.refs) == ("A → B", ["A", "B"]) and d.shape() == "moves"

    def test_a_name_realised_on_demand_in_another_module_is_noise(self) -> None:
        d = cmp([rec("foo.eq_1", m="A", rs=True)], [rec("foo.eq_1", m="B", rs=True)])
        assert only(d, "aux").note == "realised in B (was A)"

    def test_a_generated_addition_names_its_source(self) -> None:
        d = cmp([rec("mul")], [rec("mul"), rec("add", x="mul", g=True)])
        it = only(d, "added")
        assert (it.note, it.refs) == ("generated from mul", ["mul"])

    def test_where_a_declaration_is_is_never_a_change(self) -> None:
        assert not cmp([rec("foo", l=1), {"mod": "M", "src": "1"}],
                       [rec("foo", l=9), {"mod": "M", "src": "2"}]).items


class TestModules:
    def test_a_deleted_module(self) -> None:
        base = [rec("foo"), {"mod": "Gone"},
                {"mod": "Old", "dm": {"since": "2026-01-01", "text": None}}]
        d = cmp(base, [rec("foo")])
        assert only_classes(d, "mod-removed", "mod-dep-removed")
        assert d.items["mod-removed"][0].warn == "module deleted with no `deprecated_module`"
        assert d.items["mod-dep-removed"][0].note == "deprecated 2026-01-01"
        assert d.shape() == "mixed: removals + deprecation removal"

    def test_a_module_newly_deprecated_names_its_replacement(self) -> None:
        head = [rec("foo"), {"mod": "Old", "dm": {"since": None, "text": None},
                             "imp": ["Init", "New.A", "New.B"]}]
        it = only(cmp([rec("foo"), {"mod": "Old"}], head), "mod-deprecated")
        assert (it.note, it.refs, it.warn) == ("now imports New.A, New.B", ["New.A", "New.B"],
                                               "no `since` date")

    def test_a_new_module_of_moved_declarations_is_a_split(self) -> None:
        base = [rec("a", m="A"), rec("b", m="A"), {"mod": "A"}]
        head = [rec("a", m="A"), rec("b", m="B"), rec("c", m="C"), {"mod": "A"}, {"mod": "B"},
                {"mod": "C"}]
        d = cmp(base, head)
        assert d.items["mod-split"][0].note == "receives 1 moved declarations"
        assert names(d, "mod-added") == ["C"]

    def test_a_module_docstring(self) -> None:
        base = [rec("a"), {"mod": "A", "md": "1"}, {"mod": "B", "md": "1"}]
        head = [rec("a"), {"mod": "A", "md": "2"}, {"mod": "B"}]
        assert [it.note for it in cmp(base, head).items["mod-doc"]] == ["changed", "lost"]


def structure(n: str) -> list[dict]:
    """An inductive type with one constructor, and some of what Lean makes for them."""
    return [rec(n, k="ind"), rec(f"{n}.mk", k="ctor"), rec(f"{n}.rec", k="rec"),
            rec(f"{n}.casesOn", k="def"), rec(f"{n}.noConfusion", k="def"),
            rec(f"{n}.mk.inj"), rec(f"{n}.mk.injEq"), rec(f"{n}.mk.sizeOf_spec")]


MADE_FOR_FOO = ["Foo.casesOn", "Foo.mk.inj", "Foo.mk.injEq", "Foo.mk.sizeOf_spec",
                "Foo.noConfusion", "Foo.rec"]


class TestKindChange:
    """mathlib4 `d3b6d8f438c` made `NumberField.InfinitePlace.Completion`, a structure, an
    abbreviation: the 10 names Lean had made for it read as 10 removals without a
    deprecation."""

    def test_listed_not_flagged_with_what_lean_made(self) -> None:
        base = [*structure("Foo"), rec("Foo.below_1", k="def"), rec("Foo.val", k="def"),
                rec("Foo.delab", k="def"), rec("Foo.mul_1")]
        d = cmp(base, [rec("Foo", k="def")])
        it = d.items["kind"][0]
        assert (it.note, it.warn) == ("was an inductive type, now a definition", "")
        assert it.made_by_lean == sorted(["Foo.below_1", *MADE_FOR_FOO])
        # The constructor, a field and what was written by hand are still removals.
        assert names(d, "removed") == ["Foo.delab", "Foo.mk", "Foo.mul_1", "Foo.val"]
        assert not d.folded and d.shape() == "mixed: removals + kind changes"

    def test_a_constructor_kept_as_a_definition_is_not_listed_again(self) -> None:
        d = cmp(structure("Foo"), [rec("Foo", k="def"), rec("Foo.mk", k="def")])
        assert only(d, "kind").name == "Foo" and d.shape() == "kind changes"

    def test_a_definition_made_a_class(self) -> None:
        d = cmp([rec("Foo", k="def")], structure("Foo"))
        it = only_classes(d, "kind", "added").items["kind"][0]
        assert it.note == "was a definition, now an inductive type"
        assert it.made_by_lean == MADE_FOR_FOO and names(d, "added") == ["Foo.mk"]

    def test_a_structure_kept_as_a_deprecated_alias(self) -> None:
        d = cmp([*structure("Foo"), rec("Bar", k="ind")],
                [alias("Foo", "Bar", k="def"), rec("Bar", k="ind")])
        assert only_classes(d, "deprecated", "removed").items["deprecated"][0].made_by_lean \
            == MADE_FOR_FOO
        assert names(d, "removed") == ["Foo.mk"]

    def test_what_lean_makes_for_a_type_that_stays_one_is_not_moved(self) -> None:
        d = cmp(structure("Foo"), [*structure("Foo"), rec("Foo.ctorIdx", k="def")])
        assert names(only_classes(d, "added"), "added") == ["Foo.ctorIdx"]

    def test_without_kinds_nothing_changes_kind(self) -> None:
        d = cmp(structure("Foo"), [rec("Foo", k="def")], head_fields=without("k"))
        assert names(only_classes(d, "removed"), "removed") == [
            "Foo.casesOn", "Foo.mk", "Foo.noConfusion", "Foo.rec"]
        assert d.folded == {"Foo.mk": ["Foo.mk.inj", "Foo.mk.injEq", "Foo.mk.sizeOf_spec"]}

    def test_in_the_json(self) -> None:
        out = compareApi.to_json(cmp(structure("Foo"), [rec("Foo", k="def"),
                                                        rec("Foo.mk", k="def")]))
        assert out["classes"] == {"kind": [{
            "name": "Foo", "note": "was an inductive type, now a definition",
            "made_by_lean": MADE_FOR_FOO}]}


class TestFieldsNotCompared:
    """A field that only one side has, or has at another version, reads as unchanged."""

    def test_a_field_one_side_lacks_is_not_a_change(self) -> None:
        base = [rec("foo", a=["simp"], d="1", p=True)]
        d = cmp(base, [rec("foo")], head_fields=without("a", "d", "p"))
        assert not d.items and not d.has("a") and d.has("dep", "rs")

    def test_nor_is_one_at_another_version(self) -> None:
        base, head = dump([rec("foo", a=["simp"])]), dump([rec("foo", a=["simp", "grind"])])
        head.fields["a"] = 2
        assert not compare(base, head).items

    def test_a_base_with_names_only_still_gives_names_and_moves(self) -> None:
        """A merge base whose dump predates these fields: nothing is flagged."""
        base = [rec("old"), rec("moved", m="A"), {"mod": "Gone"}]
        head = [rec("moved", m="B", a=["simp"]), alias("new", "x"), {"mod": "B"}]
        d = cmp(base, head, base_fields=("n", "k", "m", "mod"))
        assert only_classes(d, "removed", "added", "moved", "mod-removed", "mod-split")
        assert not d.warnings() and d.title("removed") == "removed or renamed"

    @pytest.mark.parametrize("field", ["dep", "rs"])
    def test_a_removal_is_flagged_only_when_its_fields_are_compared(self, field: str) -> None:
        d = cmp([rec("old"), rec("kept")], [rec("kept")], head_fields=without(field))
        assert names(d, "removed") == ["old"] and not d.warnings()

    def test_a_deleted_module_likewise(self) -> None:
        d = cmp([rec("a"), {"mod": "Gone"}], [rec("a")], base_fields=without("dm"))
        assert names(d, "mod-removed") == ["Gone"] and not d.warnings()
        assert d.title("mod-removed") == "modules deleted"

    def test_without_attributes_an_instance_is_a_removal_like_any_other(self) -> None:
        d = cmp([rec("instFoo", a=["instance:1000"]), rec("kept")], [rec("kept")],
                head_fields=without("a"))
        assert only(d, "removed").warn == "removed or renamed without a deprecation"

    def test_a_field_this_script_does_not_know_is_ignored(self) -> None:
        fields = (*FIELDS, "t")
        d = cmp([rec("foo", t="1")], [rec("foo", t="2")], base_fields=fields, head_fields=fields)
        assert not d.items and "t" not in d.base["foo"]


class TestUntrustedValues:
    """The dump is the PR's own build's: a value of the wrong type stops the comparison."""

    def test_an_empty_value_written_out_reads_as_left_out(self) -> None:
        base = [rec("foo")]
        head = [rec("foo", d=None, a=[], p=False, x=None, g=False, dep=None, al=None, rs=False),
                {"mod": "M", "dm": None, "md": None, "imp": []}]
        assert not cmp(base + [{"mod": "M"}], head).items

    @pytest.mark.parametrize("field, value", [
        ("k", 1), ("m", ["M"]), ("d", 5), ("a", "simp"), ("a", ["simp", 1]), ("p", "yes"),
        ("p", 1), ("x", {"n": "foo"}), ("g", 1), ("rs", "true"), ("l", "7"), ("l", True),
        ("dep", "new"), ("dep", {"to": "new"}), ("dep", {"to": 1, "since": None, "text": None}),
        ("dep", {"to": None, "since": None, "text": None, "more": None}),
        ("al", {"k": "plain"}), ("al", {"k": "sideways", "of": "new"}),
        ("al", {"k": "plain", "of": None}), ("al", {"k": ["plain"], "of": "new"}),
    ])
    def test_a_malformed_declaration_field(self, field: str, value) -> None:
        with pytest.raises(ApiDumpError, match=f"`{field}`") as e:
            cmp([rec("foo")], [rec("foo", **{field: value})])
        assert "foo" not in str(e.value)

    @pytest.mark.parametrize("field, value", [
        ("dm", "2026-01-01"), ("dm", {"since": 1, "text": None}), ("md", 1), ("imp", "Init"),
        ("imp", [None]), ("src", 0),
    ])
    def test_a_malformed_module_field(self, field: str, value) -> None:
        with pytest.raises(ApiDumpError, match=f"`{field}`"):
            cmp([rec("foo"), {"mod": "M"}], [rec("foo"), {"mod": "M", field: value}])

    def test_a_malformed_field_that_is_not_compared_is_dropped_unread(self) -> None:
        d = cmp([rec("foo")], [rec("foo", a="simp")], base_fields=without("a"))
        assert not d.items


class TestCli:
    def _write(self, p: Path, rows: list[dict]) -> Path:
        header = {"schema": 1, "fields": {f: 1 for f in FIELDS}}
        p.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
        return p

    def test_prints_the_classes_as_json(self, tmp_path: Path, capsys) -> None:
        base = self._write(tmp_path / "base.jsonl", [rec("old"), rec("Foo", k="ind"),
                                                     rec("Foo.mk", k="ctor")])
        (tmp_path / "head").mkdir()
        self._write(tmp_path / "head" / "api.jsonl", [alias("old", "new", since="2026-07-01"),
                                                      rec("new")])
        assert compareApi.main([str(base), str(tmp_path / "head"), "--date", "2026-06-01"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["shape"] == "mixed: removals + deprecations"
        assert out["declarations"] == {"base": 3, "head": 2}
        assert out["classes"] == {
            "removed": [{"name": "Foo", "warn": "removed or renamed without a deprecation",
                         "folded": ["Foo.mk"]}],
            "deprecated": [{"name": "old", "note": "alias → new (new name)",
                            "warn": "`since` 2026-07-01 is after the PR's date 2026-06-01",
                            "since": "2026-07-01", "refs": ["new"]}]}

    def test_a_dump_that_cannot_be_read(self, tmp_path: Path, capsys) -> None:
        base = self._write(tmp_path / "base.jsonl", [rec("foo")])
        (tmp_path / "head.jsonl").write_text("not json\n")
        assert compareApi.main([str(base), str(tmp_path / "head.jsonl")]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "compareApi.py: the head dump: line 1 is not valid JSON\n"

    def test_a_malformed_value(self, tmp_path: Path, capsys) -> None:
        base = self._write(tmp_path / "base.jsonl", [rec("foo")])
        head = self._write(tmp_path / "head.jsonl", [rec("foo", a="<script>")])
        assert compareApi.main([str(base), str(head)]) == 1
        assert capsys.readouterr().err == ("compareApi.py: a row's `a` is not what that field "
                                           "holds\n")
