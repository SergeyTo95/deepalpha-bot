"""Public Treasury diagnostics, independent of custodial signing permissions."""
from services import treasury_service as treasury
from services.ton_chain_service import get_ton_balance, validate_ton_address, get_toncenter_configuration_status


def payment_health(*, with_balance=False):
    result = treasury.get_public_treasury_address()
    network = get_toncenter_configuration_status()
    status = {"ready": False, "reason": result.get("error") or "", "address": result.get("address") or "",
              "network": network.get("network"), "incoming_enabled": treasury.incoming_enabled(),
              "balance_nano": None, "balance_error": ""}
    if not result.get("ok"):
        return status
    configured_network = str(result.get("network") or "").strip().lower()
    configured_network = {"-239": "mainnet", "-3": "testnet"}.get(configured_network, configured_network)
    if not network.get("network_valid") or configured_network != network.get("network"):
        status["reason"] = "treasury_network_mismatch"
        return status
    if not validate_ton_address(status["address"]):
        status["reason"] = "invalid_treasury_address"
        return status
    status["ready"] = status["incoming_enabled"]
    status["reason"] = "" if status["ready"] else "treasury_incoming_disabled"
    if with_balance:
        try:
            status["balance_nano"] = int(get_ton_balance(status["address"]))
        except Exception:
            status["balance_error"] = "balance_unavailable"
    return status


def format_health(status):
    from services.ton_chain_service import nano_to_ton_display
    explanations = {
        "treasury_not_configured": "Главный кошелёк не назначен. Откройте Gram Wallets → настройка Treasury.",
        "treasury_conflict": "Найдено несколько главных кошельков. Приём заблокирован до устранения конфликта.",
        "treasury_lookup_failed": "Не удалось прочитать настройки главного кошелька.",
        "treasury_network_mismatch": "Сеть главного кошелька не совпадает с сетью платёжного обработчика.",
        "invalid_treasury_address": "Адрес главного кошелька некорректен.",
        "treasury_incoming_disabled": "Кошелёк назначен, но приём платежей отключён (TREASURY_INCOMING_ENABLED).",
    }
    lines = ["💎 Главный кошелёк и приём Gram", "", "Адрес: " + (status.get("address") or "не назначен"),
             "Сеть: " + str(status.get("network") or "не определена")]
    balance = status.get("balance_nano")
    lines.append("Баланс: " + (nano_to_ton_display(balance) + " Gram" if balance is not None else "не получен"))
    if status.get("balance_error"):
        lines.append("Сервис сети не вернул баланс. Это не означает нулевой баланс.")
    lines.extend(["", "Приём платежей: готов" if status.get("ready") else
                  explanations.get(status.get("reason"), "Приём платежей недоступен."), "",
                  "Для приёма Gram достаточно публичного адреса. Закрытый ключ нужен только для отправки.",
                  "Готовность настройки не заменяет проверку реальной оплаты и зачисления."])
    return "\n".join(lines)
