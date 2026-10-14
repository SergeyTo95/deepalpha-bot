"""User-facing connection inventory; planned providers never appear connected."""
import os
from urllib.parse import urlsplit
from services import velia_agent_google_calendar_service as calendar

# Grouped products are one connection card, not a promise of every API operation.
CONNECTORS = (
 ('notion','Notion','knowledge','Заметки, выбранные страницы и материалы проектов.'),
 ('google-calendar','Google Calendar','personal','Встречи, свободное время и план дня.'),
 ('google-drive','Google Drive и Docs','knowledge','Найти и разобрать выбранные документы.'),
 ('google-sheets','Google Sheets','business','Сравнить данные и подготовить выводы по таблицам.'),
 ('todoist','Todoist','personal','Задачи, сроки и списки дел.'),
 ('outlook','Outlook и календарь','personal','Письма, черновики ответов и встречи.'),
 ('onedrive','OneDrive','knowledge','Файлы и документы Microsoft.'),
 ('gmail','Gmail','personal','Найти письма и подготовить ответы.'),
 ('dropbox','Dropbox','knowledge','Найти файлы и сравнить документы.'),
 ('trello','Trello','team','Карточки, сроки и обзоры проектов.'),
 ('slack','Slack','team','Доступные обсуждения и итоги работы команды.'),
 ('hubspot','HubSpot','business','История клиентов и записи CRM.'),
 ('canva','Canva','content','Доступные шаблоны и экспорт оформления.'),
 ('microsoft-todo','Microsoft To Do','personal','Личные задачи и списки покупок.'),
 ('google-tasks','Google Tasks','personal','Списки задач и сроки.'),
 ('onenote','OneNote','knowledge','Заметки, конспекты и сохранённые идеи.'),
 ('zotero','Zotero','education','Исследования, источники и библиография.'),
 ('google-classroom','Google Classroom','education','Доступные учебные задания и сроки.'),
 ('zoom','Zoom','team','Встречи и итоги доступных записей.'),
 ('youtube','YouTube','content','Для авторов: свой канал, плейлисты и описания. Не даёт расшифровку любого видео.'),
 ('figma','Figma','content','Разбор доступных макетов и комментариев.'),
 ('asana','Asana','team','Задания, сроки и обзоры проектов.'),
 ('clickup','ClickUp','team','Задачи и статусы рабочих проектов.'),
 ('monday','monday.com','team','Рабочие доски и процессы команды.'),
 ('jira','Jira','team','Обращения и задачи в доступных проектах.'),
 ('shopify','Shopify','business','Для владельцев магазинов: товары, заказы и продажи.'),
 ('wordpress','WordPress.com','content','Статьи, черновики и материалы сайта.'),
 ('zendesk','Zendesk','business','Обращения клиентов и подготовка ответов.'),
)


def list_connections(user_id):
    from services import velia_notion_connection_service as notion
    from services import velia_cloud_connection_service as cloud
    result=[]
    for key,name,category,description in CONNECTORS:
        row=dict(id=key,name=name,category=category,description=description,
                 connected=False,configured=False,available=False,status='planned',
                 status_text='Готовится',read_only=True)
        adapter=calendar if key=='google-calendar' else notion if key=='notion' else None
        implemented=adapter is not None or key in cloud.SPECS
        if implemented:
            try:
                status=cloud.connection_status(int(user_id),key) if key in cloud.SPECS else adapter.connection_status(int(user_id))
                row.update(connected=bool(status.get('connected')),
                           configured=bool(status.get('configured')),
                           available=bool(status.get('configured')))
                if key=='google-calendar':
                    redirect=urlsplit(os.getenv('VELIA_GOOGLE_CALENDAR_APP_REDIRECT',''))
                    row['available']=row['available'] and redirect.scheme=='https' and bool(redirect.hostname) and not redirect.username
                row['status']='connected' if row['connected'] else 'available' if row['available'] else 'requires_setup'
                row['status_text']='Подключено' if row['connected'] else 'Можно подключить' if row['available'] else 'Нужно настроить приложение VELIA'
                row['read_only']=key!='google-calendar'
            except Exception:
                row.update(status='unavailable',status_text='Не удалось проверить подключение',available=False)
        result.append(row)
    return result
