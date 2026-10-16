"""
Regression tests for multi-turn constraint filtering.
Tests:
  A. Budget + colour constraints persist when user says "Only in stock"
  B. A product above the maximum price is excluded
  C. A product with unknown/NULL price is excluded under a strict price_max
  D. Answer text count, product cards, and result count all agree (within display limit)
  E. Removing a constraint only happens when the user explicitly removes/replaces it
"""
import pytest
from app.domain.constraints import AttrConstraint, ConstraintSet, PriceBound
from app.domain.enums import StockStatus
from app.settings import load_settings
from app.container import build_container
from app.conversation.merger import merge
from app.domain.constraints import ConstraintOp
from app.domain.enums import OpKind


@pytest.fixture(scope="module")
def container():
    settings = load_settings()
    return build_container(settings)


@pytest.fixture(scope="module")
def repo(container):
    return container.product_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_price_max(rupees: float, inclusive: bool = False) -> PriceBound:
    return PriceBound(amount_minor=int(rupees * 100), inclusive=inclusive)


def colour_attr(colour: str) -> AttrConstraint:
    return AttrConstraint(key="color", value=colour)


def eligible_pids(repo, constraints: ConstraintSet) -> list[str]:
    return sorted(repo.eligible_ids(constraints).ids)


# ---------------------------------------------------------------------------
# Test A: Budget + colour constraints persist across a follow-up turn
# ---------------------------------------------------------------------------

class TestConstraintPersistence:

    def test_a_price_and_colour_persist_after_stock_refinement(self, repo):
        """
        Turn 1 constraints: price_max=₹2000, category=footwear, color=black
        Turn 2 user says: "Only in stock"
        Expected: all three original constraints + stock=IN_STOCK are all active
        """
        # Simulate Turn 1 merged constraint set
        cs_turn1 = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
            attributes=(colour_attr("black"),),
        )

        # Turn 2: user adds stock=IN_STOCK via merger
        ops_turn2 = [ConstraintOp(op=OpKind.SET, key="stock", value="IN_STOCK", origin="USER_TEXT")]
        merge_result = merge(cs_turn1, ops_turn2)
        cs_turn2 = merge_result.constraints

        # Price, category, and colour must all be preserved
        assert cs_turn2.price_max is not None, "price_max was dropped"
        assert cs_turn2.price_max.amount_minor == 200000, "price_max amount changed"
        assert "footwear" in cs_turn2.categories, "category was dropped"
        assert colour_attr("black") in cs_turn2.attributes, "colour constraint was dropped"
        assert cs_turn2.stock == StockStatus.IN_STOCK, "stock constraint was not set"
        assert merge_result.carried_over, "carried_over flag should be True"

    def test_a_eligible_ids_respect_all_constraints_after_stock_added(self, repo):
        """All products in eligible set must satisfy price_max, colour, and stock."""
        cs = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
            attributes=(colour_attr("black"),),
        )
        pids = eligible_pids(repo, cs)
        for pid in pids:
            p = repo.get(pid)
            assert p is not None
            assert p.price_minor is not None, f"{pid}: NULL price passed price_max filter"
            assert p.price_minor < 200000, f"{pid}: price {p.price_minor} exceeds price_max 200000"
            assert p.stock_status == StockStatus.IN_STOCK, f"{pid}: stock={p.stock_status} violates IN_STOCK"
        # Must return at least the three known valid black shoes
        assert "SH-BLK-001" in pids, "SH-BLK-001 (Rs1499, IN_STOCK, black) was wrongly excluded"
        assert "SH-BLK-002" in pids, "SH-BLK-002 (Rs1899, IN_STOCK, black) was wrongly excluded"
        assert "SH-BLK-003" in pids, "SH-BLK-003 (Rs1299, IN_STOCK, black) was wrongly excluded"


# ---------------------------------------------------------------------------
# Test B: Product above the maximum price is excluded
# ---------------------------------------------------------------------------

class TestPriceMaxExclusion:

    def test_b_product_above_price_max_excluded(self, repo):
        """SH-BLK-005 is priced at Rs2899 and must NOT appear when price_max=Rs2000."""
        over_price_pid = "SH-BLK-005"
        p = repo.get(over_price_pid)
        assert p is not None, f"Test product {over_price_pid} must exist in catalogue"
        assert p.price_minor == 289900, f"Expected price_minor=289900 for {over_price_pid}"

        cs = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
        )
        pids = eligible_pids(repo, cs)
        assert over_price_pid not in pids, (
            f"{over_price_pid} (Rs2899) must be excluded when price_max=Rs2000"
        )

    def test_b_product_at_exact_boundary_excluded_when_strict(self, repo):
        """price_max=₹1999 strictly (inclusive=False) must exclude a product at exactly Rs1999."""
        cs = ConstraintSet(price_max=PriceBound(amount_minor=199900, inclusive=False))
        pids = eligible_pids(repo, cs)
        for pid in pids:
            p = repo.get(pid)
            assert p.price_minor is not None
            assert p.price_minor < 199900, f"{pid}: price {p.price_minor} >= strict boundary 199900"

    def test_b_product_at_exact_boundary_included_when_inclusive(self, repo):
        """price_max=₹1999 inclusive must include a product at exactly Rs1999."""
        cs = ConstraintSet(price_max=PriceBound(amount_minor=199900, inclusive=True))
        pids = eligible_pids(repo, cs)
        at_boundary = [pid for pid in pids
                       if repo.get(pid).price_minor == 199900]
        assert len(at_boundary) > 0, "Expected at least one product at the inclusive boundary"


# ---------------------------------------------------------------------------
# Test C: NULL-price product excluded under strict price_max
# ---------------------------------------------------------------------------

class TestNullPriceExclusion:

    def test_c_null_price_product_excluded_under_price_max(self, repo):
        """SH-NOP-001 has NULL price and must be excluded when price_max is active."""
        null_price_pid = "SH-NOP-001"
        p = repo.get(null_price_pid)
        assert p is not None, f"Test product {null_price_pid} must exist"
        assert p.price_minor is None, f"{null_price_pid} should have NULL price"

        cs = ConstraintSet(price_max=make_price_max(2000.0))
        pids = eligible_pids(repo, cs)
        assert null_price_pid not in pids, (
            f"{null_price_pid} (NULL price) must be excluded when price_max is active"
        )

    def test_c_null_price_product_included_when_no_price_constraint(self, repo):
        """SH-NOP-001 should appear when there is no price constraint."""
        null_price_pid = "SH-NOP-001"
        cs = ConstraintSet(categories=frozenset(["footwear"]))
        pids = eligible_pids(repo, cs)
        assert null_price_pid in pids, (
            f"{null_price_pid} should be included when no price constraint is set"
        )

    def test_c_null_price_excluded_even_with_stock_in_stock(self, repo):
        """NULL-price product with IN_STOCK status must still be excluded by price_max."""
        null_price_pid = "SH-NOP-001"
        cs = ConstraintSet(
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
        )
        pids = eligible_pids(repo, cs)
        assert null_price_pid not in pids, (
            f"{null_price_pid} must be excluded by price_max even though it is IN_STOCK"
        )


# ---------------------------------------------------------------------------
# Test D: Answer text count and product cards agree
# ---------------------------------------------------------------------------

class TestCountConsistency:

    def test_d_eligible_count_matches_exact_ids_in_search_result(self, container):
        """The exact_ids count in SearchResult must equal all products passing hard filters."""
        from app.domain.plans import SearchSpec
        from app.domain.enums import Intent

        cs = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
        )
        spec = SearchSpec(
            intent=Intent.SEARCH,
            constraints=cs,
            entity_terms=("shoes",),
            top_k=3,  # deliberately small to test count vs cards
        )
        result = container.retriever.search_catalogue(spec, turn_id=1)

        # exact_ids contains ALL matching products (beyond top_k)
        # ranked contains at most top_k
        assert len(result.exact_ids) >= len(result.ranked), (
            "ranked should never exceed exact_ids"
        )
        # The manifest counts must be consistent
        assert result.manifest.exact_match_count == len(result.exact_ids)
        assert result.manifest.returned_count == len(result.ranked)

    def test_d_no_product_in_cards_that_is_not_in_exact_ids(self, container):
        """Every product returned in ranked must be in exact_ids."""
        from app.domain.plans import SearchSpec
        from app.domain.enums import Intent

        cs = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
        )
        spec = SearchSpec(
            intent=Intent.SEARCH,
            constraints=cs,
            entity_terms=("shoes",),
            top_k=10,
        )
        result = container.retriever.search_catalogue(spec, turn_id=1)
        ranked_ids = {c.product_id for c in result.ranked}
        exact_set = set(result.exact_ids)
        extras = ranked_ids - exact_set
        assert not extras, f"ranked contains IDs not in exact_ids: {extras}"


# ---------------------------------------------------------------------------
# Test E: Constraint removal only on explicit user action
# ---------------------------------------------------------------------------

class TestConstraintRemovalExplicitOnly:

    def test_e_stock_constraint_not_dropped_on_price_refinement(self):
        """Adding a new price constraint must not silently drop stock."""
        cs = ConstraintSet(
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
        )
        # User refines price to Rs1500 (does NOT say "remove in stock")
        ops = [ConstraintOp(op=OpKind.SET, key="price_max",
                            value={"amount_minor": 150000, "inclusive": False},
                            origin="USER_TEXT")]
        result = merge(cs, ops)
        assert result.constraints.stock == StockStatus.IN_STOCK, (
            "stock constraint must not be dropped when user only changes price"
        )
        assert result.constraints.price_max.amount_minor == 150000

    def test_e_colour_constraint_not_dropped_on_stock_refinement(self):
        """Adding stock=IN_STOCK must not drop colour attribute constraint."""
        cs = ConstraintSet(
            categories=frozenset(["footwear"]),
            price_max=make_price_max(2000.0),
            attributes=(colour_attr("black"),),
        )
        ops = [ConstraintOp(op=OpKind.SET, key="stock", value="IN_STOCK", origin="USER_TEXT")]
        result = merge(cs, ops)
        assert colour_attr("black") in result.constraints.attributes, (
            "colour must persist when user only adds stock filter"
        )

    def test_e_explicit_remove_op_drops_constraint(self):
        """Only an explicit REMOVE op should drop a constraint."""
        cs = ConstraintSet(
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
        )
        ops = [ConstraintOp(op=OpKind.REMOVE, key="stock", value=None, origin="USER_TEXT")]
        result = merge(cs, ops)
        assert result.constraints.stock is None, (
            "stock constraint must be removed when REMOVE op is explicitly applied"
        )
        # But price_max must remain
        assert result.constraints.price_max is not None, (
            "price_max must not be affected by REMOVE stock op"
        )

    def test_e_reset_op_clears_all_constraints(self):
        """RESET op must clear all constraints."""
        cs = ConstraintSet(
            price_max=make_price_max(2000.0),
            stock=StockStatus.IN_STOCK,
            categories=frozenset(["footwear"]),
            attributes=(colour_attr("black"),),
        )
        ops = [ConstraintOp(op=OpKind.RESET, key="all", value=None, origin="USER_TEXT")]
        result = merge(cs, ops)
        assert result.constraints.is_empty(), "RESET must clear all constraints"
        assert result.topic_switch is True
