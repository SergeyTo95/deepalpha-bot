import {validateZip} from './table-tools.mjs';

const MAX_TEXT = 180000;
const textTypes = new Set(['text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document']);

export const canExtractLocalText = file => textTypes.has(file.type) && !/\.csv$/iu.test(file.name);

export async function localDocumentText(file) {
  if (!canExtractLocalText(file)) throw Error('Поддерживаются текстовые файлы и DOCX.');
  let text;
  if (file.type === 'text/plain') {
    if (file.size > MAX_TEXT * 4) throw Error('Файл слишком большой для обработки текста на этом устройстве.');
    text = await file.text();
  } else {
    const {unzipSync} = await import('/web/productivity-index.mjs');
    const bytes = await file.arrayBuffer();
    validateZip(bytes);
    const archive = unzipSync(new Uint8Array(bytes), {
      filter: item => item.name === 'word/document.xml' && item.originalSize <= 4 * 1024 * 1024,
    });
    if (!archive['word/document.xml']) throw Error('В документе нет доступного текста.');
    const xml = new DOMParser().parseFromString(new TextDecoder().decode(archive['word/document.xml']), 'application/xml');
    if (xml.querySelector('parsererror')) throw Error('Не удалось прочитать DOCX.');
    const paragraphs = xml.getElementsByTagNameNS('http://schemas.openxmlformats.org/wordprocessingml/2006/main','p');
    text = Array.from(paragraphs, paragraph =>
      Array.from(paragraph.getElementsByTagNameNS(paragraph.namespaceURI,'t'), node => node.textContent).join('')
    ).join('\n');
  }
  if (!text.trim()) throw Error('В документе нет текста.');
  if (text.length > MAX_TEXT) throw Error('В документе больше 180 000 знаков.');
  return text;
}

export function redactContactData(text) {
  let emails = 0;
  let phones = 0;
  const masked = text.replace(/[\p{L}\p{N}._%+-]+@[\p{L}\p{N}.-]+\.[\p{L}]{2,63}/gu, () => {
    emails++;
    return '[почта скрыта]';
  }).replace(/(?<![\p{L}\p{N}])\+?\d[\d ().-]{8,22}\d(?![\p{L}\p{N}])/gu, value => {
    const length = value.replace(/\D/gu,'').length;
    if (length < 10 || length > 15 || (!value.startsWith('+') && !/[ ()-]/u.test(value))) return value;
    if (/^\d{4}-\d{2}-\d{2}$/u.test(value)) return value;
    phones++;
    return '[телефон скрыт]';
  });
  return {masked, emails, phones};
}
