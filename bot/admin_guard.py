"""Enforce owner/private-chat authorization at registration for every admin handler."""
from functools import wraps


def private_actor(event, *, callback=False):
    user = getattr(event, "from_user", None)
    message = getattr(event, "message", None) if callback else event
    chat = getattr(message, "chat", None)
    if not user or not chat or getattr(chat, "type", None) != "private":
        return None
    uid = getattr(user, "id", None)
    return uid if uid and getattr(chat, "id", None) == uid else None


def can_open_view_during_moderation(event, *, callback=False):
    uid = private_actor(event, callback=callback)
    if uid is None:
        return False
    if callback:
        allowed = getattr(event, "data", None) in {"deepalpha_view:overview", "deepalpha_view:wallet"}
    else:
        command = str(getattr(event, "text", "") or "").split(maxsplit=1)
        allowed = bool(command and command[0].split("@", 1)[0] == "/admin")
    if not allowed:
        return False
    from services.deepalpha_admin_access import can_view_project
    return can_view_project(uid)


class OwnerDispatcher:
    def __init__(self, dispatcher, is_owner):
        self.dispatcher = dispatcher
        self.is_owner = is_owner

    def _decorate(self, register, callback):
        def decorate(handler):
            @wraps(handler)
            async def guarded(event, *args, **kwargs):
                uid = private_actor(event, callback=callback)
                if uid is None or not self.is_owner(uid):
                    if callback:
                        await event.answer("Доступ только для суперадминистратора в личном чате.", show_alert=True)
                    return None
                return await handler(event, *args, **kwargs)
            return register(guarded)
        return decorate

    def message_handler(self, *args, **kwargs):
        return self._decorate(self.dispatcher.message_handler(*args, **kwargs), False)

    def callback_query_handler(self, *args, **kwargs):
        return self._decorate(self.dispatcher.callback_query_handler(*args, **kwargs), True)
