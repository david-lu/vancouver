import assert from 'node:assert/strict';
import {ownership} from '../lib/finance.ts';
const basic=ownership(1000000,20,0,25,6000,0,100,200);
assert.equal(basic.mortgage,800000/300);assert.equal(basic.transfer,18000);assert.equal(basic.premium,0);assert.equal(basic.minimumDown,75000);
const insured=ownership(600000,10,4.5,25,0,0,0,0);assert.equal(insured.premium,540000*.031);assert.equal(insured.valid,true);
assert.equal(ownership(1500000,10,4.5,25,0,0,0,0).valid,false);
assert.equal(ownership(1000000,100,4.5,25,0,0,0,0).mortgage,0);
assert.equal(ownership(4000000,20,4.5,25,0,0,0,0).transfer,118000);
console.log('FINANCE_CHECKS_PASSED');
