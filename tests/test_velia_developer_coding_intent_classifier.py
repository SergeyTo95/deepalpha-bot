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
