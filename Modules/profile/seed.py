"""
seed.py — загрузка данных из fixtures/ в ProfileStore.

Функции:
  load_taxonomy(store)       — загружает taxonomy.json, идемпотентно.
  load_example_account(store) — загружает example_account.json, идемпотентно.
"""
import json
from pathlib import Path

from storage import ProfileStore

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_taxonomy(store: ProfileStore) -> int:
    """Загрузить ниши из taxonomy.json. Возвращает кол-во добавленных записей."""
    data = json.loads((FIXTURES_DIR / "taxonomy.json").read_text(encoding="utf-8"))
    return store.seed_taxonomy(data)


def load_example_account(store: ProfileStore) -> bool:
    """Загрузить example_account.json. Идемпотентно — не перезаписывает если уже существует.
    Возвращает True если аккаунт был создан, False если уже существовал.
    """
    data = json.loads((FIXTURES_DIR / "example_account.json").read_text(encoding="utf-8"))
    acc_data = data["account"]
    account_id = acc_data["id"]

    # Идемпотентность: не перезаписываем существующий аккаунт
    if store.get_account(account_id) is not None:
        return False

    store.create_account_with_id(
        account_id=account_id,
        name=acc_data["name"],
        niche_slug=acc_data.get("niche_slug"),
    )

    if "brand_book" in data:
        bb = data["brand_book"]
        tone = bb.get("tone", {})
        store.upsert_brand_book(
            account_id,
            formality=tone.get("formality"),
            energy=tone.get("energy"),
            humor=tone.get("humor"),
            expertise=tone.get("expertise"),
            forbidden_words=bb.get("forbidden_words"),
            cta=bb.get("cta"),
            extra=bb.get("extra"),
        )

    if "audience" in data:
        aud = data["audience"]
        store.upsert_audience(
            account_id,
            age_range=aud.get("age_range"),
            geography=aud.get("geography"),
            gender=aud.get("gender"),
            expertise_level=aud.get("expertise_level"),
            pain_points=aud.get("pain_points"),
            desires=aud.get("desires"),
            extra=aud.get("extra"),
        )

    if "prompt_profile" in data:
        pp = data["prompt_profile"]
        store.create_prompt_profile(
            account_id,
            version=pp["version"],
            system_prompt=pp["system_prompt"],
            modifiers=pp.get("modifiers"),
            hard_constraints=pp.get("hard_constraints"),
            soft_constraints=pp.get("soft_constraints"),
        )

    return True
