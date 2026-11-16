from services import velia_developer_coding_service as coding


def test_read_only_repository_questions_are_not_coding_requests():
    messages = [
        "Проверь в нашем репозитории, где создаётся mobile API route",
        "Найди, где подключается VELIA Developer",
        "Объясни архитектуру backend и покажи файлы",
        "Где создается этот endpoint?",
        "Show where this route is created in the repository",
    ]
    assert all(not coding.is_coding_request(message) for message in messages)


def test_explicit_mutation_requests_are_coding_requests():
    messages = [
        "Добавь новый mobile API route в backend",
        "Исправь баг в функции авторизации",
        "Создай файл сервиса и тесты",
        "Нужно реализовать новый endpoint в репозитории",
        "Хочу изменить Android код и добавить экран",
        "Implement the feature in the backend code",
        "Refactor this service and update its tests",
    ]
    assert all(coding.is_coding_request(message) for message in messages)


def test_text_humanization_is_not_a_coding_request_when_pasted_article_mentions_code():
    article = (
        "Велия, перепиши этот текст по-человечески, используя STE80 Humanizer. "
        "Сохрани все факты, даты и ссылки.\n\n"
        "Компания использует Python, API, сервисы и GitHub. В новом посте "
        "авторы обсуждают код и разработчиков. Ничего не внедряй."
    )
    assert coding.is_prose_edit_request(article) is True
    assert coding.is_coding_request(article) is False
    assert coding.should_handle(article) is False
    assert coding.is_prose_edit_request("Humanize this article.\n\nBuild API services") is True
    assert coding.is_coding_request("Humanize this article.\n\nBuild API services") is False


def test_explicit_code_changes_still_reach_coding_agent():
    samples = (
        "Перепиши функцию авторизации в Python backend",
        "Перепиши код сервиса velia_chat_service.py и добавь тесты",
        "Исправь API endpoint в репозитории",
        "Create a backend service that humanizes text in Python",
    )
    assert all(not coding.is_prose_edit_request(item) for item in samples)
    assert all(coding.is_coding_request(item) for item in samples)
