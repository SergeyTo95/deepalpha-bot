import asyncio
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from bot.admin_guard import OwnerDispatcher, private_actor
from services import deepalpha_admin_access as access
from services import deepalpha_manager_payout_service as payouts
from services.gram_payment_health import payment_health, format_health
from services.ton_chain_service import nano_to_ton_display


class TeamStates(StatesGroup):
    telegram_id = State()
    share_value = State()
    payout_amount = State()


def _keyboard(*buttons):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for title, data in buttons:
        kb.add(types.InlineKeyboardButton(title, callback_data=data))
    return kb


def viewer_keyboard():
    return _keyboard(("📊 Обзор проекта", "deepalpha_view:overview"),
                     ("💎 Главный кошелёк", "deepalpha_view:wallet"),
                     ("💸 Моя доля и выплаты", "deepalpha_view:payouts"))


async def open_admin_panel(message):
    uid = private_actor(message)
    if uid is None:
        return
    if access.is_admin_user(uid):
        from bot.admin import admin_main_kb
        await message.answer("⚙️ DeepAlpha · суперадминистратор", reply_markup=admin_main_kb())
    elif await asyncio.to_thread(access.can_view_project, uid):
        await message.answer("📊 DeepAlpha · управляющий\nДоступ только на просмотр информации о проекте.",
                             reply_markup=viewer_keyboard())


def _error(exc):
    return {
        "invalid_telegram_id": "Введите числовой Telegram ID.",
        "owner_role_is_fixed": "Роль суперадминистратора закреплена за владельцем.",
        "user_must_start_bot": "Пользователь должен сначала открыть бота и нажать /start.",
        "viewer_limit_reached": "Уже назначены два управляющих. Сначала отзовите один доступ.",
        "invalid_share": "Доля должна быть от 0% до 100%.",
        "total_share_exceeds_100": "Суммарная доля управляющих не может превышать 100%.",
        "viewer_not_active": "Управляющий не активен.",
        "invalid_amount": "Введите корректную сумму Gram больше нуля.",
        "amount_exceeds_period_revenue": "Сумма не может быть больше подтверждённой выручки за этот месяц.",
        "payout_not_editable": "Эту выплату уже нельзя редактировать.",
        "payout_not_approvable": "Эту выплату уже нельзя подтвердить повторно.",
        "payout_not_cancellable": "Эту выплату уже нельзя отменить.",
        "treasury_outgoing_disabled": "Исходящие переводы Treasury сейчас отключены защитным флагом.",
        "internal_wallet_required": "Не удалось подготовить внутренний кошелёк управляющего.",
    }.get(str(exc), "Не удалось выполнить действие. Попробуйте позже.")


def _pct(bps):
    value = Decimal(int(bps or 0)) / Decimal(100)
    return format(value.normalize(), "f") + "%"


async def _team_panel(message, actor_id):
    viewers = await asyncio.to_thread(access.list_viewers, actor_id)
    text = ["👥 Управляющие DeepAlpha", "", "Вы — суперадминистратор. Управляющие видят проект и свои выплаты, но не могут переводить средства или менять доли."]
    buttons = [("💸 Месячные выплаты", "deepalpha_payouts"), ("➕ Добавить управляющего", "deepalpha_team:add")]
    for viewer in viewers:
        uid = viewer["user_id"]
        label = viewer["username"] or viewer["first_name"] or str(uid)
        text.append(f"• {escape(label)} · <code>{uid}</code> · доля {_pct(viewer['share_bps'])}")
        buttons.append((f"Изменить долю {uid} · {_pct(viewer['share_bps'])}", f"deepalpha_team:share:{uid}"))
        buttons.append((f"Отозвать доступ: {uid}", f"deepalpha_team:revoke:{uid}"))
    if not viewers:
        text.append("Управляющие ещё не назначены.")
    buttons.append(("⬅️ Админка", "admin_back"))
    await message.answer("\n".join(text), parse_mode="HTML", reply_markup=_keyboard(*buttons))


async def _payout_panel(message, actor_id):
    rows = await asyncio.to_thread(payouts.list_pending, actor_id)
    text = ["💸 Выплаты управляющим", "", "Каждая выплата создаётся автоматически за предыдущий календарный месяц. Перевод выполняется только после вашего подтверждения."]
    buttons = []
    if not rows:
        text.append("\nОжидающих выплат нет.")
    for row in rows:
        label = row.get("username") or row.get("first_name") or str(row["manager_user_id"])
        text.append(
            f"\n#{row['id']} · {escape(str(label))}\n"
            f"Период: {row['period_start']} — {row['period_end']}\n"
            f"Подтверждённая выручка: {payouts.format_gram(row['gross_revenue_nano'])} Gram\n"
            f"Доля: {_pct(row['share_bps'])}\n"
            f"К выплате: {payouts.format_gram(row['amount_nano'])} Gram\n"
            f"Статус: {escape(str(row['status']))}"
        )
        if row["status"] == "pending":
            buttons.extend([
                (f"✅ Подтвердить #{row['id']} · {payouts.format_gram(row['amount_nano'])} Gram", f"deepalpha_payout:approve:{row['id']}"),
                (f"✏️ Изменить сумму #{row['id']}", f"deepalpha_payout:edit:{row['id']}"),
                (f"🚫 Отменить #{row['id']}", f"deepalpha_payout:cancel:{row['id']}"),
            ])
    buttons.append(("⬅️ Управляющие", "deepalpha_team"))
    await message.answer("\n".join(text), parse_mode="HTML", reply_markup=_keyboard(*buttons))


async def _preview(message, actor_id, raw_id):
    try:
        candidate = await asyncio.to_thread(access.viewer_candidate, actor_id, str(raw_id).strip())
    except (ValueError, PermissionError) as exc:
        await message.answer(_error(exc))
        return
    uid = candidate["user_id"]
    label = candidate["username"] or candidate["first_name"] or "без имени"
    await message.answer(
        f"Назначить управляющего?\n{escape(label)}\nTelegram ID: <code>{uid}</code>\n\n"
        "Доступ к проекту — только просмотр. Долю и выплаты контролирует только суперадминистратор.",
        parse_mode="HTML", reply_markup=_keyboard(("✅ Дать доступ на просмотр", f"deepalpha_team:grant:{uid}"),
                                                 ("Отмена", "deepalpha_team")))


def register_viewers(dp):
    owner = OwnerDispatcher(dp, access.is_admin_user)

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("deepalpha_view:"), state="*")
    async def view(callback):
        uid = private_actor(callback, callback=True)
        if uid is None or not await asyncio.to_thread(access.can_view_project, uid):
            await callback.answer("Доступ отсутствует или отозван.", show_alert=True)
            return
        await callback.answer()
        try:
            if callback.data == "deepalpha_view:wallet":
                text = await asyncio.to_thread(lambda: format_health(payment_health(with_balance=True)))
            elif callback.data == "deepalpha_view:overview":
                data = await asyncio.to_thread(access.project_snapshot, uid)
                text = ("📊 DeepAlpha · сводка\n\n"
                        f"Пользователей в базе: {data['users']}\nАнализов: {data['analyses']}\n"
                        f"Токенов на балансах: {data['tokens']}\nОплаченных операций: {data['payments']}\n"
                        f"Подтверждённые поступления: {nano_to_ton_display(data['revenue_nano'])} Gram\n"
                        f"Платежей в ожидании: {data['pending']}\n\nДоступ только на просмотр.")
            elif callback.data == "deepalpha_view:payouts":
                profile = await asyncio.to_thread(access.viewer_profile, uid)
                from services.ton_wallet_service import get_or_create_user_ton_wallet
                wallet = await asyncio.to_thread(get_or_create_user_ton_wallet, uid)
                history = await asyncio.to_thread(payouts.list_for_manager, uid, 6)
                lines = ["💸 Моя доля и выплаты", "", f"Текущая доля: {_pct(profile['share_bps'])}",
                         "Внутренний кошелёк: " + (str(wallet.get('wallet_address') or 'недоступен') if wallet.get('ok') else 'недоступен')]
                if history:
                    lines.append("\nПоследние периоды:")
                    for row in history:
                        lines.append(f"• {row['period_start']}: {payouts.format_gram(row['amount_nano'])} Gram · {row['status']}")
                else:
                    lines.append("\nВыплат пока нет.")
                text = "\n".join(lines)
            else:
                return
            if not await asyncio.to_thread(access.can_view_project, uid):
                return
            await callback.message.answer(escape(text), parse_mode="HTML", reply_markup=viewer_keyboard())
        except Exception:
            await callback.message.answer("Не удалось загрузить сводку. Попробуйте позже.")

    @owner.message_handler(commands=["admin_viewer_add"], state="*")
    async def add_command(message, state: FSMContext):
        await state.finish()
        parts = str(message.text or "").split()
        if len(parts) != 2:
            await message.answer("Формат: /admin_viewer_add Telegram_ID")
            return
        await _preview(message, message.from_user.id, parts[1])

    @owner.callback_query_handler(lambda c: c.data == "deepalpha_payouts" or str(c.data or "").startswith("deepalpha_payout:"), state="*")
    async def payout_actions(callback, state: FSMContext):
        await callback.answer()
        action = str(callback.data or "")
        uid = callback.from_user.id
        if action == "deepalpha_payouts":
            await state.finish()
            await _payout_panel(callback.message, uid)
            return
        parts = action.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            return
        payout_id = int(parts[2])
        if parts[1] == "edit":
            await state.finish()
            await state.update_data(payout_id=payout_id)
            await TeamStates.payout_amount.set()
            await callback.message.answer("Введите новую сумму выплаты в Gram, например 125.5. Сумма не может превышать подтверждённую выручку этого периода.")
            return
        try:
            if parts[1] == "approve":
                result = await asyncio.to_thread(payouts.approve_and_send, uid, payout_id)
                if result.get("ok"):
                    await callback.message.answer("✅ Перевод подтверждён вами и отправлен. Финальный статус станет Paid после on-chain проверки.")
                else:
                    await callback.message.answer("⚠️ Перевод не завершён: " + _error(result.get("error")))
            elif parts[1] == "cancel":
                await asyncio.to_thread(payouts.cancel, uid, payout_id)
                await callback.message.answer("Выплата отменена.")
        except Exception as exc:
            await callback.message.answer(_error(exc))
        await _payout_panel(callback.message, uid)

    @owner.callback_query_handler(lambda c: c.data == "deepalpha_team" or str(c.data or "").startswith("deepalpha_team:"), state="*")
    async def team(callback, state: FSMContext):
        await callback.answer()
        await state.finish()
        action = str(callback.data or "")
        uid = callback.from_user.id
        if action == "deepalpha_team:add":
            await TeamStates.telegram_id.set()
            await callback.message.answer("Пришлите Telegram ID управляющего. Перед назначением будет показано имя из базы бота.")
            return
        if action.startswith("deepalpha_team:share:"):
            target = action.rsplit(":", 1)[-1]
            if not target.isdigit():
                return
            await state.update_data(target_user_id=int(target))
            await TeamStates.share_value.set()
            await callback.message.answer("Введите долю управляющего в процентах, например 20 или 10.5. Изменение применяется к будущим месячным расчётам.")
            return
        if action.startswith(("deepalpha_team:grant:", "deepalpha_team:revoke:")):
            try:
                await asyncio.to_thread(access.set_viewer, uid, action.rsplit(":", 1)[-1], active=":grant:" in action)
            except Exception as exc:
                await callback.message.answer(_error(exc))
                return
        await _team_panel(callback.message, uid)

    @owner.message_handler(state=TeamStates.telegram_id)
    async def id_input(message, state: FSMContext):
        await state.finish()
        await _preview(message, message.from_user.id, message.text)

    @owner.message_handler(state=TeamStates.share_value)
    async def share_input(message, state: FSMContext):
        data = await state.get_data()
        await state.finish()
        try:
            value = Decimal(str(message.text or "").strip().replace(",", "."))
            bps = int(value * 100)
            if value < 0 or value > 100 or Decimal(bps) / Decimal(100) != value:
                raise ValueError("invalid_share")
            await asyncio.to_thread(access.set_share_bps, message.from_user.id, int(data["target_user_id"]), bps)
            await message.answer(f"✅ Доля обновлена: {_pct(bps)}. Она будет использоваться в будущих месячных расчётах.")
        except (InvalidOperation, KeyError, ValueError, PermissionError) as exc:
            await message.answer(_error(exc))
        await _team_panel(message, message.from_user.id)

    @owner.message_handler(state=TeamStates.payout_amount)
    async def payout_amount_input(message, state: FSMContext):
        data = await state.get_data()
        await state.finish()
        try:
            row = await asyncio.to_thread(payouts.set_amount, message.from_user.id, int(data["payout_id"]), str(message.text or "").strip())
            await message.answer(f"✅ Сумма #{row['id']} изменена на {payouts.format_gram(row['amount_nano'])} Gram. Перевода ещё не было — требуется ваше подтверждение.")
        except Exception as exc:
            await message.answer(_error(exc))
        await _payout_panel(message, message.from_user.id)

    @owner.callback_query_handler(lambda c: c.data == "admin_gram_payment_health", state="*")
    async def health(callback):
        await callback.answer()
        text = await asyncio.to_thread(lambda: format_health(payment_health(with_balance=True)))
        await callback.message.answer(escape(text), parse_mode="HTML", reply_markup=_keyboard(
            ("🔄 Обновить", "admin_gram_payment_health"), ("⬅️ Gram Wallets", "admin_gram_wallets")))
