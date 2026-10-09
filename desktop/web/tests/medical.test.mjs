import test from 'node:test';
import assert from 'node:assert/strict';
import {medicalUploadFormat,medicalFindings} from '../features.mjs';
test('medical files reject unrelated gzip, documents and empty studies',()=>{for(const name of ['photo.jpg','report.pdf','archive.gz'])assert.throws(()=>medicalUploadFormat({name,size:10}));assert.throws(()=>medicalUploadFormat({name:'ct.zip',size:0}));assert.equal(medicalUploadFormat({name:'CT.NII.GZ',size:1}),'nifti_gz');assert.equal(medicalUploadFormat({name:'ct.zip',size:1}),'dicom_zip');});
test('medical scores retain uncalibrated semantics and reject invalid values',()=>{assert.deepEqual(medicalFindings({findings:[{score:0.9}]}),[]);const rows=medicalFindings({score_semantics:'model_score_not_calibrated_probability',findings:[{organ:'Pancreas',finding:'Example',score:0.7},{score:NaN},{score:2}]});assert.equal(rows.length,1);assert.equal(rows[0].score,'0.700');});
