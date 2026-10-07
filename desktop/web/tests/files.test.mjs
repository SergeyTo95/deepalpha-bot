import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareFile,fileSize} from '../files.mjs';
test('text formats normalize to the server-supported MIME without changing bytes',async()=>{
  for(const name of ['REPORT.CSV','note.md','data.json','note.txt']){const file=new File(['<script>example</script>'],name,{type:'application/json'}),prepared=prepareFile(file);assert.equal(prepared.type,'text/plain');assert.equal(await prepared.text(),await file.text());assert.equal(prepared.name,name);}
});
test('unsupported, empty and oversized files fail before upload',()=>{
  assert.throws(()=>prepareFile(new File(['x'],'sheet.xlsx')),/XLSX/);
  assert.throws(()=>prepareFile(new File([],'empty.txt')),/Пустой/);
  assert.throws(()=>prepareFile(new File([new Uint8Array(15*1024*1024+1)],'big.pdf')),/15 МБ/);
  assert.throws(()=>prepareFile(new File(['<svg/>'],'image.svg')),/Поддерживаются/);
  assert.equal(prepareFile(new File(['x'],'photo.webp')).type,'image/webp');
});
test('size labels are bounded and readable',()=>{assert.equal(fileSize(1024),'1 КБ');assert.equal(fileSize(1024*1024),'1.0 МБ');});
