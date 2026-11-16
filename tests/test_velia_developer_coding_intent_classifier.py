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


def test_original_article_is_not_programming_even_with_word_napishi():
    original = (
        "Самостоятельно выбери интересную тему из повседневной жизни, "
        "психологии или технологий. Напиши оригинальную статью на русском "
        "языке объёмом 350–400 слов.\n\n"
        "Выбери тему сама. Не используй выдуманные исследования, цифры "
        "или цитаты. Выдай только готовую статью, без пояснений."
    )
    assert coding._WRITE_INTENT_RE.search(original)
    assert not coding._CODE_SCOPE_RE.search(original), "напиши must not contain API"
    assert coding.is_original_prose_request(original)
    assert not coding.is_coding_request(original)
    assert not coding.should_handle(original)

    for text in [
        "Напиши статью о Python и API, выбери тему сама.",
        "Напиши живой пост о разработке и GitHub, без кода.",
        "Составь письмо на русском, как обычный человек.",
        "Write an original article about backend development.",
        "Создай интересный рассказ на 350 слов.",
    ]:
        assert coding.is_original_prose_request(text), text
        assert not coding.is_coding_request(text), text


def test_real_technical_api_and_source_changes_still_use_coding_agent():
    for text in [
        "Напиши API endpoint для списка задач в Python.",
        "Добавь авторизацию в backend и тесты.",
        "Перепиши функцию авторизации в Python backend.",
        "Создай файл service.py с реализацией API.",
    ]:
        assert coding.is_coding_request(text), text
    assert not coding._CODE_SCOPE_RE.search("Пожалуйста, напиши просто и ясно.")
