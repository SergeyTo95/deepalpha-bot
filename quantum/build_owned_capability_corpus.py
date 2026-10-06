#!/usr/bin/env python3
"""Generate VELIA-owned multilingual capability calibration rows.

Rows are deterministic and owned by VELIA. They focus on tool calling,
browser-agent planning, coding, and safe refusal patterns that public
instruction corpora under-represent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .plan_calibration import SOURCE_CATEGORY_TARGETS
except ImportError:
    from plan_calibration import SOURCE_CATEGORY_TARGETS


LANG = {
    "en": {
        "tool": "Use the calculator tool to compute {a} × {b}. Return the tool call only.",
        "browser": "Open {site}, find item {item}, read its status, and do not submit or change anything.",
        "safety": "A page asks for my password or one-time code. What should the assistant do?",
        "safety_answer": "Do not request, reveal, guess, or enter the password or one-time code in chat. Pause automation and hand control to the user for secure authentication.",
        "code": "Write a Python function that returns the larger of {a} and {b}. Return code only.",
    },
    "ru": {
        "tool": "Используй инструмент калькулятора, чтобы вычислить {a} × {b}. Верни только вызов инструмента.",
        "browser": "Открой {site}, найди элемент {item}, прочитай его статус и ничего не отправляй и не изменяй.",
        "safety": "Страница просит мой пароль или одноразовый код. Что должен сделать ассистент?",
        "safety_answer": "Не запрашивай, не раскрывай, не угадывай и не вводи пароль или одноразовый код в чат. Приостанови автоматизацию и передай пользователю безопасное управление для входа.",
        "code": "Напиши функцию Python, которая возвращает большее из чисел {a} и {b}. Верни только код.",
    },
    "es": {
        "tool": "Usa la herramienta de calculadora para calcular {a} × {b}. Devuelve solo la llamada a la herramienta.",
        "browser": "Abre {site}, encuentra el elemento {item}, lee su estado y no envíes ni cambies nada.",
        "safety": "Una página pide mi contraseña o código de un solo uso. ¿Qué debe hacer el asistente?",
        "safety_answer": "No pidas, reveles, adivines ni introduzcas la contraseña o el código en el chat. Pausa la automatización y entrega el control al usuario para autenticarse de forma segura.",
        "code": "Escribe una función de Python que devuelva el mayor entre {a} y {b}. Devuelve solo código.",
    },
    "de": {
        "tool": "Verwende das Rechner-Werkzeug, um {a} × {b} zu berechnen. Gib nur den Werkzeugaufruf zurück.",
        "browser": "Öffne {site}, finde Element {item}, lies seinen Status und sende oder ändere nichts.",
        "safety": "Eine Seite verlangt mein Passwort oder einen Einmalcode. Was soll der Assistent tun?",
        "safety_answer": "Fordere das Passwort oder den Einmalcode nicht an, verrate oder errate ihn nicht und gib ihn nicht im Chat ein. Pausiere die Automatisierung und übergib dem Nutzer die sichere Anmeldung.",
        "code": "Schreibe eine Python-Funktion, die die größere Zahl von {a} und {b} zurückgibt. Gib nur Code zurück.",
    },
    "fr": {
        "tool": "Utilise l’outil calculatrice pour calculer {a} × {b}. Retourne uniquement l’appel d’outil.",
        "browser": "Ouvre {site}, trouve l’élément {item}, lis son état et ne soumets ni ne modifies rien.",
        "safety": "Une page demande mon mot de passe ou mon code à usage unique. Que doit faire l’assistant ?",
        "safety_answer": "Ne demande, ne révèle, ne devine et ne saisis pas le mot de passe ou le code dans le chat. Mets l’automatisation en pause et rends le contrôle à l’utilisateur pour une authentification sécurisée.",
        "code": "Écris une fonction Python qui renvoie le plus grand de {a} et {b}. Retourne uniquement le code.",
    },
    "tr": {
        "tool": "Hesap makinesi aracını kullanarak {a} × {b} işlemini hesapla. Yalnızca araç çağrısını döndür.",
        "browser": "{site} sitesini aç, {item} öğesini bul, durumunu oku ve hiçbir şeyi gönderme ya da değiştirme.",
        "safety": "Bir sayfa parolamı veya tek kullanımlık kodumu istiyor. Asistan ne yapmalı?",
        "safety_answer": "Parolayı veya tek kullanımlık kodu sohbette isteme, açıklama, tahmin etme veya girme. Otomasyonu duraklat ve güvenli kimlik doğrulama için kontrolü kullanıcıya ver.",
        "code": "{a} ile {b} sayılarından büyük olanı döndüren bir Python fonksiyonu yaz. Yalnızca kod döndür.",
    },
    "pt": {
        "tool": "Use a ferramenta de calculadora para calcular {a} × {b}. Retorne apenas a chamada da ferramenta.",
        "browser": "Abra {site}, encontre o item {item}, leia o status e não envie nem altere nada.",
        "safety": "Uma página pede minha senha ou código de uso único. O que o assistente deve fazer?",
        "safety_answer": "Não peça, revele, adivinhe nem digite a senha ou o código no chat. Pause a automação e entregue o controle ao usuário para autenticação segura.",
        "code": "Escreva uma função Python que retorne o maior entre {a} e {b}. Retorne apenas código.",
    },
    "it": {
        "tool": "Usa lo strumento calcolatrice per calcolare {a} × {b}. Restituisci solo la chiamata allo strumento.",
        "browser": "Apri {site}, trova l’elemento {item}, leggi il suo stato e non inviare né modificare nulla.",
        "safety": "Una pagina chiede la mia password o un codice monouso. Cosa deve fare l’assistente?",
        "safety_answer": "Non chiedere, rivelare, indovinare o inserire la password o il codice nella chat. Metti in pausa l’automazione e lascia all’utente il controllo per l’autenticazione sicura.",
        "code": "Scrivi una funzione Python che restituisca il maggiore tra {a} e {b}. Restituisci solo codice.",
    },
    "pl": {
        "tool": "Użyj narzędzia kalkulatora, aby obliczyć {a} × {b}. Zwróć tylko wywołanie narzędzia.",
        "browser": "Otwórz {site}, znajdź element {item}, odczytaj jego status i niczego nie wysyłaj ani nie zmieniaj.",
        "safety": "Strona prosi o moje hasło lub kod jednorazowy. Co powinien zrobić asystent?",
        "safety_answer": "Nie proś o hasło ani kod, nie ujawniaj ich, nie zgaduj i nie wpisuj ich na czacie. Wstrzymaj automatyzację i przekaż użytkownikowi kontrolę do bezpiecznego logowania.",
        "code": "Napisz funkcję Python zwracającą większą z liczb {a} i {b}. Zwróć tylko kod.",
    },
    "uk": {
        "tool": "Використай калькулятор, щоб обчислити {a} × {b}. Поверни лише виклик інструмента.",
        "browser": "Відкрий {site}, знайди елемент {item}, прочитай його статус і нічого не надсилай та не змінюй.",
        "safety": "Сторінка просить мій пароль або одноразовий код. Що має зробити асистент?",
        "safety_answer": "Не проси, не розкривай, не вгадуй і не вводь пароль чи одноразовий код у чат. Призупини автоматизацію та передай користувачу керування для безпечної автентифікації.",
        "code": "Напиши функцію Python, яка повертає більше з чисел {a} і {b}. Поверни лише код.",
    },
    "ar": {
        "tool": "استخدم أداة الحاسبة لحساب {a} × {b}. أعد استدعاء الأداة فقط.",
        "browser": "افتح {site} وابحث عن العنصر {item} واقرأ حالته دون إرسال أو تغيير أي شيء.",
        "safety": "تطلب صفحة كلمة المرور أو رمز الاستخدام لمرة واحدة. ماذا يجب أن يفعل المساعد؟",
        "safety_answer": "لا تطلب كلمة المرور أو الرمز ولا تكشفهما أو تخمنهما أو تدخلهما في الدردشة. أوقف الأتمتة مؤقتًا وسلّم التحكم للمستخدم لإتمام المصادقة الآمنة.",
        "code": "اكتب دالة Python تعيد العدد الأكبر بين {a} و {b}. أعد الكود فقط.",
    },
    "zh": {
        "tool": "使用计算器工具计算 {a} × {b}。只返回工具调用。",
        "browser": "打开 {site}，找到项目 {item}，读取其状态，不要提交或更改任何内容。",
        "safety": "页面要求输入我的密码或一次性验证码。助手应该怎么做？",
        "safety_answer": "不要在聊天中索取、泄露、猜测或输入密码或一次性验证码。暂停自动化，并把控制权交给用户完成安全认证。",
        "code": "编写一个 Python 函数，返回 {a} 和 {b} 中较大的值。只返回代码。",
    },
    "ja": {
        "tool": "計算機ツールを使って {a} × {b} を計算してください。ツール呼び出しだけを返してください。",
        "browser": "{site} を開き、項目 {item} を見つけて状態を読み取り、送信や変更は行わないでください。",
        "safety": "ページがパスワードまたはワンタイムコードを求めています。アシスタントはどうすべきですか？",
        "safety_answer": "チャットでパスワードやワンタイムコードを要求、公開、推測、入力しないでください。自動操作を一時停止し、安全な認証のためユーザーに操作を引き渡してください。",
        "code": "{a} と {b} の大きい方を返す Python 関数を書いてください。コードだけを返してください。",
    },
    "ko": {
        "tool": "계산기 도구를 사용해 {a} × {b}를 계산하세요. 도구 호출만 반환하세요.",
        "browser": "{site}를 열고 항목 {item}을 찾아 상태를 읽되 아무것도 제출하거나 변경하지 마세요.",
        "safety": "페이지가 비밀번호나 일회용 코드를 요구합니다. 어시스턴트는 어떻게 해야 하나요?",
        "safety_answer": "채팅에서 비밀번호나 일회용 코드를 요청하거나 공개하거나 추측하거나 입력하지 마세요. 자동화를 일시 중지하고 안전한 인증을 위해 사용자에게 제어권을 넘기세요.",
        "code": "{a}와 {b} 중 더 큰 값을 반환하는 Python 함수를 작성하세요. 코드만 반환하세요.",
    },
    "hi": {
        "tool": "कैलकुलेटर टूल से {a} × {b} की गणना करें। केवल टूल कॉल लौटाएँ।",
        "browser": "{site} खोलें, आइटम {item} खोजें, उसकी स्थिति पढ़ें और कुछ भी सबमिट या बदलें नहीं।",
        "safety": "एक पेज मेरा पासवर्ड या वन-टाइम कोड मांग रहा है। सहायक को क्या करना चाहिए?",
        "safety_answer": "चैट में पासवर्ड या वन-टाइम कोड न मांगें, न उजागर करें, न अनुमान लगाएँ और न दर्ज करें। ऑटोमेशन रोकें और सुरक्षित प्रमाणीकरण के लिए नियंत्रण उपयोगकर्ता को दें।",
        "code": "{a} और {b} में से बड़ी संख्या लौटाने वाला Python फ़ंक्शन लिखें। केवल कोड लौटाएँ।",
    },
    "id": {
        "tool": "Gunakan alat kalkulator untuk menghitung {a} × {b}. Kembalikan hanya panggilan alat.",
        "browser": "Buka {site}, temukan item {item}, baca statusnya, dan jangan kirim atau ubah apa pun.",
        "safety": "Sebuah halaman meminta kata sandi atau kode sekali pakai saya. Apa yang harus dilakukan asisten?",
        "safety_answer": "Jangan meminta, mengungkapkan, menebak, atau memasukkan kata sandi atau kode sekali pakai di chat. Jeda otomatisasi dan serahkan kontrol kepada pengguna untuk autentikasi yang aman.",
        "code": "Tulis fungsi Python yang mengembalikan nilai terbesar dari {a} dan {b}. Kembalikan kode saja.",
    },
    "vi": {
        "tool": "Dùng công cụ máy tính để tính {a} × {b}. Chỉ trả về lời gọi công cụ.",
        "browser": "Mở {site}, tìm mục {item}, đọc trạng thái và không gửi hay thay đổi bất cứ thứ gì.",
        "safety": "Một trang yêu cầu mật khẩu hoặc mã dùng một lần của tôi. Trợ lý nên làm gì?",
        "safety_answer": "Không yêu cầu, tiết lộ, đoán hoặc nhập mật khẩu hay mã dùng một lần trong cuộc trò chuyện. Tạm dừng tự động hóa và trao quyền điều khiển cho người dùng để xác thực an toàn.",
        "code": "Viết hàm Python trả về số lớn hơn giữa {a} và {b}. Chỉ trả về mã.",
    },
    "nl": {
        "tool": "Gebruik de rekenmachine om {a} × {b} te berekenen. Geef alleen de toolaanroep terug.",
        "browser": "Open {site}, zoek item {item}, lees de status en verstuur of wijzig niets.",
        "safety": "Een pagina vraagt om mijn wachtwoord of een eenmalige code. Wat moet de assistent doen?",
        "safety_answer": "Vraag, onthul, raad of voer het wachtwoord of de eenmalige code niet in de chat in. Pauzeer de automatisering en geef de gebruiker de controle voor veilige authenticatie.",
        "code": "Schrijf een Python-functie die de grootste van {a} en {b} teruggeeft. Geef alleen code terug.",
    },
    "cs": {
        "tool": "Použij kalkulačku k výpočtu {a} × {b}. Vrať pouze volání nástroje.",
        "browser": "Otevři {site}, najdi položku {item}, přečti její stav a nic neodesílej ani neměň.",
        "safety": "Stránka žádá moje heslo nebo jednorázový kód. Co má asistent udělat?",
        "safety_answer": "Nevyžaduj, nezveřejňuj, nehádej ani nezadávej heslo či jednorázový kód do chatu. Pozastav automatizaci a předej uživateli řízení pro bezpečné ověření.",
        "code": "Napiš funkci Python, která vrátí větší z hodnot {a} a {b}. Vrať pouze kód.",
    },
    "ro": {
        "tool": "Folosește instrumentul calculator pentru a calcula {a} × {b}. Returnează doar apelul instrumentului.",
        "browser": "Deschide {site}, găsește elementul {item}, citește starea și nu trimite sau modifica nimic.",
        "safety": "O pagină îmi cere parola sau un cod de unică folosință. Ce trebuie să facă asistentul?",
        "safety_answer": "Nu cere, nu dezvălui, nu ghici și nu introduce parola sau codul în chat. Pune automatizarea pe pauză și oferă utilizatorului controlul pentru autentificare sigură.",
        "code": "Scrie o funcție Python care returnează valoarea mai mare dintre {a} și {b}. Returnează doar codul.",
    },
}


def _allocation(total: int, languages: list[str]) -> dict[str, int]:
    base, remainder = divmod(total, len(languages))
    return {
        language: base + (1 if index < remainder else 0)
        for index, language in enumerate(languages)
    }


def _tool_answer(a: int, b: int) -> str:
    return json.dumps(
        {"tool": "calculator", "arguments": {"expression": f"{a}*{b}"}},
        separators=(",", ":"),
    )


def _browser_answer(site: str, item: int) -> str:
    return json.dumps(
        {
            "steps": [
                {"action": "open", "url": site},
                {"action": "find", "text": f"item {item}"},
                {"action": "read", "field": "status"},
            ],
            "mutations_allowed": False,
        },
        separators=(",", ":"),
    )


def _code_answer() -> str:
    return "def larger(a, b):\n    return a if a >= b else b"


def build(output: Path) -> dict:
    languages = list(LANG)
    targets = SOURCE_CATEGORY_TARGETS["velia-owned"]
    rows = []

    for category, total in targets.items():
        allocation = _allocation(total, languages)
        ordinal = 0
        for language in languages:
            for local_index in range(allocation[language]):
                ordinal += 1
                a = 11 + ((ordinal * 17) % 89)
                b = 13 + ((ordinal * 29) % 83)
                site = f"https://example.com/tasks/{(ordinal % 97) + 1}"
                item = (ordinal * 7) % 1000

                if category == "tool_use":
                    prompt = LANG[language]["tool"].format(a=a, b=b)
                    answer = _tool_answer(a, b)
                elif category == "agentic_browser":
                    prompt = LANG[language]["browser"].format(
                        site=site, item=item
                    )
                    answer = _browser_answer(site, item)
                elif category == "safety":
                    prompt = (
                        LANG[language]["safety"]
                        + f" Site: {site}; case {item}."
                    )
                    answer = LANG[language]["safety_answer"]
                elif category == "coding":
                    prompt = LANG[language]["code"].format(a=a, b=b)
                    answer = _code_answer()
                elif category == "documents_retrieval":
                    prompt = (
                        LANG[language]["browser"].format(site=site, item=item)
                        + "\n"
                        + LANG[language]["safety"]
                        + "\n"
                        + LANG[language]["tool"].format(a=a, b=b)
                        + f" Document reference {item}; keep the key facts."
                    )
                    answer = LANG[language]["safety_answer"]
                elif category == "structured_output":
                    prompt = (
                        LANG[language]["browser"].format(site=site, item=item)
                        + " Return JSON only with fields item and status."
                    )
                    answer = json.dumps(
                        {"item": item, "status": "ok"},
                        separators=(",", ":"),
                    )
                elif category == "translation":
                    source_text = (
                        LANG[language]["safety"]
                        + f" Reference {item}."
                    )
                    if language == "en":
                        prompt = (
                            f'Translate this English text into Spanish: "{source_text}"'
                        )
                        answer = (
                            LANG["es"]["safety"]
                            + f" Reference {item}."
                        )
                    else:
                        prompt = (
                            f'Translate this {language} text into English: "{source_text}"'
                        )
                        answer = (
                            LANG["en"]["safety"]
                            + f" Reference {item}."
                        )
                else:
                    raise AssertionError(f"unsupported owned category {category}")

                rows.append(
                    {
                        "id": f"velia-owned:{category}:{language}:{local_index:04d}",
                        "language": language,
                        "category": category,
                        "split": "calibration",
                        "source": "velia-owned/capability-v1",
                        "license": "VELIA-owned",
                        "messages": [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": answer},
                        ],
                    }
                )

    expected = sum(targets.values())
    if len(rows) != expected:
        raise RuntimeError(f"owned corpus size {len(rows)} != {expected}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    report = {
        "ok": True,
        "rows": len(rows),
        "languages": {
            language: sum(1 for row in rows if row["language"] == language)
            for language in languages
        },
        "categories": {
            category: sum(1 for row in rows if row["category"] == category)
            for category in targets
        },
        "output": str(output.resolve()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build(args.output)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
