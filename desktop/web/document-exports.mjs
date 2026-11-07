export function documentExportControl(content, toast) {
  const group = document.createElement("span");
  group.className = "document-export";
  const label = document.createElement("label");
  label.className = "sr-only";
  label.textContent = "Формат файла";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Скачать ответ файлом");
  for (const [value, name] of [
    ["", "Скачать…"],
    ["md", "Markdown"],
    ["txt", "Текст"],
    ["docx", "Word · DOCX"],
    ["pdf", "PDF"],
    ["xlsx", "Excel · XLSX"],
    ["pptx", "Презентация · PPTX"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = name;
    select.append(option);
  }
  select.onchange = async () => {
    const format = select.value;
    if (!format) return;
    select.disabled = true;
    try {
      let blob;
      if (["md", "txt"].includes(format))
        blob = new Blob([content], { type: "text/plain;charset=utf-8" });
      else {
        const response = await fetch("/web-api/v1/documents/export", {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-Velia-Request": "1",
          },
          body: JSON.stringify({ format, content }),
          signal: AbortSignal.timeout(30000),
        });
        if (!response.ok)
          throw new Error(
            response.status === 401
              ? "Войди в Велию, чтобы скачать документ."
              : "Не удалось подготовить файл. Попробуй ещё раз.",
          );
        blob = await response.blob();
      }
      const url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = "VELIA-answer." + format;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast(error.message);
    } finally {
      select.value = "";
      select.disabled = false;
    }
  };
  group.append(label, select);
  return group;
}
