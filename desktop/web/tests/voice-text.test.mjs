import test from 'node:test';
import assert from 'node:assert/strict';
import {sentenceBoundary,pronounceSpeech} from '../voice-text.mjs';
test('streaming speech does not pause at abbreviations, initials or decimals',()=>{
 for(const text of ['Это т. е. пример. Дальше','Ул. Ленина, д. 12. Дальше','Dr. Smith is here. Next','Цена 125.50. Дальше','А. С. Пушкин. Дальше']){
  const cut=sentenceBoundary(text);assert.equal(text.slice(cut).trim(),text.endsWith('Next')?'Next':'Дальше');
 }
 assert.equal(sentenceBoundary('Это т. е.'),0);
 assert.equal(sentenceBoundary('Готово! Ещё'),7);
});
test('speech-only Russian normalization preserves quantities, negation and chat source',()=>{
 const source='Не отправляй 1 ₽ и 22 руб. до 2026-10-31, т. е. завтра.';
 assert.equal(pronounceSpeech(source,'ru-RU'),'Не отправляй 1 рубль и 22 рубля до тридцать первого октября 2026 года, то есть завтра.');
 assert.equal(source,'Не отправляй 1 ₽ и 22 руб. до 2026-10-31, т. е. завтра.');
 assert.equal(pronounceSpeech('125,5 ₽; 11 ₽; 21 ₽; 24 ₽; 125 ₽'),'125,5 рубля; 11 рублей; 21 рубль; 24 рубля; 125 рублей');
 assert.equal(pronounceSpeech('К 2026-11-03, перед 2026-11-04.'),'К третьему ноября 2026 года, перед четвёртым ноября 2026 года.');
});
test('invalid dates, other languages and long expansions remain unchanged',()=>{
 assert.equal(pronounceSpeech('2026-02-30 2025-13-01 2024-02-29'),'2026-02-30 2025-13-01 двадцать девятое февраля 2024 года');
 for(const language of ['en-US','tr-TR','ar'])assert.equal(pronounceSpeech('1 ₽ 2026-10-31',language),'1 ₽ 2026-10-31');
 const heavy='1 ₽ '.repeat(100);assert.equal(pronounceSpeech(heavy),heavy);
});
