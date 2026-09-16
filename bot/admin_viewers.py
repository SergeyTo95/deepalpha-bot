import asyncio
from html import escape

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from bot.admin_guard import OwnerDispatcher, private_actor
from services import deepalpha_admin_access as access
from services.gram_payment_health import payment_health, format_health
from services.ton_chain_service import nano_to_ton_display


class TeamStates(StatesGroup):
    telegram_id = State()


def _keyboard(*buttons):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for title, data in buttons:
        kb.add(types.InlineKeyboardButton(title, callback_data=data))
    return kb


def viewer_keyboard():
    return _keyboard(("📊 Обзор проекта", "deepalpha_view:overview"),
                     ("💎 Главный кошелёк", "deepalpha_view:wallet"))


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
    }.get(str(exc), "Не удалось изменить доступ. Попробуйте позже.")


async def _team_panel(message, actor_id):
    viewers = await asyncio.to_thread(access.list_viewers, actor_id)
    text = ["👥 Управляющие DeepAlpha", "", "Вы — суперадминистратор. Управляющие могут только смотреть сводку проекта и главный кошелёк."]
    buttons = [("➕ Добавить управляющего", "deepalpha_team:add")]
    for viewer in viewers:
        uid = viewer["user_id"]
        label = viewer["username"] or viewer["first_name"] or str(uid)
        text.append(f"• {escape(label)} · <code>{uid}</code> · просмотр")
        buttons.append((f"Отозвать доступ: {uid}", f"deepalpha_team:revoke:{uid}"))
    if not viewers:
        text.append("Управляющие ещё не назначены.")
    buttons.append(("⬅️ Админка", "admin_back"))
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
        "Только просмотр. Переводы, ключи, настройки, начисления и назначение админов недоступны.",
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
            else:
                return
            # Recheck immediately before disclosure after potentially slow I/O.
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

    @owner.callback_query_handler(lambda c: c.data == "admin_gram_payment_health", state="*")
    async def health(callback):
        await callback.answer()
        text = await asyncio.to_thread(lambda: format_health(payment_health(with_balance=True)))
        await callback.message.answer(escape(text), parse_mode="HTML", reply_markup=_keyboard(
            ("🔄 Обновить", "admin_gram_payment_health"), ("⬅️ Gram Wallets", "admin_gram_wallets")))
