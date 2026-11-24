"""Home widgets for Actual Budget (manifest ``widgets``; ctl/app_widgets.py runs them).

Budget: the viewer's own budget this month: spent against budgeted, and the categories they spend
most on, read with actualpy and their own Actual login (the one Mu3Lab keeps for their chat
connector). Actual sends the whole budget file, so it is read every half hour at most.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from actual import Actual
from actual.exceptions import ActualError
from actual.queries import get_budgets, get_transactions

from ctl.app_widgets import WidgetContext, WidgetError

CONNECTOR = "actual-budget-community"


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def budget(ctx: WidgetContext) -> dict[str, Any]:
    login = ctx.own_credentials(CONNECTOR)
    start = date.today().replace(day=1)
    end = (start.replace(day=28) + (date.resolution * 4)).replace(day=1)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="mu3lab-actual-") as folder,
            Actual(
                base_url=ctx.base,
                password=login.get("ACTUAL_PASSWORD", ""),
                file=login.get("ACTUAL_BUDGET_SYNC_ID", ""),
                data_dir=folder,
                timeout=60,
            ) as actual,
        ):
            spent: dict[str, Decimal] = defaultdict(Decimal)
            for transaction in get_transactions(actual.session, start_date=start, end_date=end, transfer=False):
                amount = transaction.get_amount()
                if amount < 0 and transaction.category and not transaction.category.is_income:
                    spent[transaction.category.name or "Other"] -= amount
            budgeted = sum((item.get_amount() for item in get_budgets(actual.session, month=start)), Decimal(0))
    except ActualError:
        raise WidgetError("Actual did not accept your Mu3Lab login, or the budget was not found.") from None
    total = sum(spent.values(), Decimal(0))
    top = sorted(spent.items(), key=lambda pair: pair[1], reverse=True)[:8]
    over = budgeted and total > budgeted
    return {
        "stats": [
            {"label": "Spent this month", "value": _money(total), "tone": "bad" if over else ""},
            {"label": "Budgeted", "value": _money(budgeted) if budgeted else "Not set"},
            *(
                [{"label": "Left", "value": _money(budgeted - total), "tone": "bad" if over else "good"}]
                if budgeted
                else []
            ),
        ],
        "items_title": "Most spent on",
        "items": [
            {
                "title": name,
                "meta": _money(amount),
                "progress": float(amount / total) if total else None,
            }
            for name, amount in top
        ],
        "empty": "Nothing spent yet this month.",
    }
