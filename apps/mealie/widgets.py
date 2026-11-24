"""Home widgets for Mealie (manifest ``widgets``; ctl/app_widgets.py runs them).

* Meal plan: today's meals and the rest of the week, with the household's recipe count.
* Shopping list: what is still to buy, ticked off right in the widget.

Read through aiomealie (the client Home Assistant uses) with the token Mu3Lab made for Mealie's
administrator (the chat connector's). Mealie's lists and plan belong to the whole household.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from typing import Any

from aiomealie import MealieClient, MealieError
from aiomealie.models import MutateShoppingItem

from ctl.app_widgets import WidgetContext, WidgetError, run

CONNECTOR = "mealie-community"
TOKEN_ENV = "MEALIE_API_TOKEN"
ORDER = ["breakfast", "lunch", "dinner", "side", "dessert", "snack", "drink"]


def _client(ctx: WidgetContext) -> MealieClient:
    token = ctx.credentials(CONNECTOR).get(TOKEN_ENV, "")
    if not token:
        raise WidgetError("Mu3Lab's Mealie token is missing; reinstall Mealie from its page in Apps.")
    return MealieClient(f"http://127.0.0.1:{ctx.app.manifest.service.local_port}", token)


def _day(when: date) -> str:
    today = date.today()
    return "Today" if when == today else "Tomorrow" if when == today + timedelta(days=1) else f"{when:%A}"


async def _plan(ctx: WidgetContext) -> dict[str, Any]:
    client = _client(ctx)
    today = date.today()
    try:
        plan = (await client.get_mealplans(start_date=today, end_date=today + timedelta(days=6))).items
        figures = await client.get_statistics()
    except MealieError:
        raise WidgetError("Mealie did not give its meal plan.") from None
    finally:
        await client.close()
    plan.sort(
        key=lambda meal: (
            meal.mealplan_date,
            ORDER.index(meal.entry_type.value) if meal.entry_type.value in ORDER else len(ORDER),
        )
    )
    items = []
    for meal in plan:
        name = meal.recipe.name if meal.recipe else meal.title or "A meal"
        items.append(
            {
                "title": name,
                "subtitle": f"{_day(meal.mealplan_date)} · {meal.entry_type.value.capitalize()}",
                "tone": "good" if meal.mealplan_date == today else "",
                "url": ctx.web(f"/g/home/r/{meal.recipe.slug}") if meal.recipe and meal.recipe.slug else "",
            }
        )
    planned_today = sum(1 for meal in plan if meal.mealplan_date == today)
    return {
        "stats": [
            {"label": "Today", "value": str(planned_today), "tone": "" if planned_today else "warn"},
            {"label": "This week", "value": str(len(plan))},
            {"label": "Recipes", "value": str(figures.total_recipes)},
        ],
        "items_title": "This week",
        "items": items,
        "empty": "Nothing planned this week. Plan a meal in Mealie.",
    }


def meal_plan(ctx: WidgetContext) -> dict[str, Any]:
    return run(_plan(ctx))


async def _shopping(ctx: WidgetContext) -> dict[str, Any]:
    client = _client(ctx)
    try:
        lists = (await client.get_shopping_lists()).items
        items = []
        left = 0
        for shopping in lists:
            for item in (await client.get_shopping_items(shopping.list_id)).items:
                if item.checked:
                    continue
                left += 1
                items.append(
                    {
                        "title": item.display or item.note or "An item",
                        "subtitle": shopping.name if len(lists) > 1 else "",
                        "check": False,
                        "action": f"{shopping.list_id}/{item.item_id}",
                    }
                )
    except MealieError:
        raise WidgetError("Mealie did not give its shopping lists.") from None
    finally:
        await client.close()
    return {
        "stats": [{"label": "To buy", "value": str(left)}, {"label": "Lists", "value": str(len(lists))}],
        "items": items,
        "empty": "Nothing left to buy.",
    }


def shopping_list(ctx: WidgetContext) -> dict[str, Any]:
    return run(_shopping(ctx))


async def _tick(ctx: WidgetContext, action: str) -> None:
    list_id, _, item_id = action.partition("/")
    client = _client(ctx)
    try:
        current = next(
            (item for item in (await client.get_shopping_items(list_id)).items if item.item_id == item_id), None
        )
        if current is None:
            raise WidgetError("That item is no longer on the list.")
        fields = {field.name for field in dataclasses.fields(MutateShoppingItem)}
        values = {name: getattr(current, name) for name in fields if hasattr(current, name)}
        await client.update_shopping_item(item_id, MutateShoppingItem(**(values | {"checked": not current.checked})))
    except MealieError:
        raise WidgetError("Mealie did not take the change.") from None
    finally:
        await client.close()


def shopping_list_action(ctx: WidgetContext, action: str) -> None:
    run(_tick(ctx, action))
