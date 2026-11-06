import test from 'node:test';
import assert from 'node:assert/strict';
import {calculateIncome, describeIncome} from '../income-planner.mjs';

const example = {
  hours: 5, goal: 20000, currency: 'RUB',
  searchMinutes: null, orderMinutes: 120, price: 2000, costs: 0,
};

test('sales time is excluded from order capacity and the required price', () => {
  const plan = calculateIncome(example);
  assert.equal(plan.search, 60);
  assert.equal(plan.work, 16 * 60);
  assert.equal(plan.orders, 8);
  assert.equal(plan.revenue, 1600000);
  assert.equal(plan.net, 1600000);
  assert.equal(plan.hourlyTarget, 125000);
  assert.equal(plan.orderTarget, 250000);
  assert.match(describeIncome(example, plan).join('\n'), /20%/);
});

test('expenses are added to the target and a partial order earns nothing', () => {
  const values = {...example, searchMinutes: 0, orderMinutes: 180, costs: 1.01};
  const plan = calculateIncome(values);
  assert.equal(plan.orders, 6);
  assert.equal(plan.net, 1199899);
  assert.equal(plan.orderTarget, 333351);
  assert.doesNotMatch(describeIncome(values, plan).join('\n'), /20%/);
});

test('an unspecified or infeasible order duration does not yield a unit price', () => {
  assert.equal(calculateIncome({...example, orderMinutes: null}).revenue, null);
  const plan = calculateIncome({...example, orderMinutes: 1000});
  assert.equal(plan.orders, 0);
  assert.equal(plan.orderTarget, null);
  assert.throws(() => calculateIncome({...example, searchMinutes: 300}), /выполнение заказов/);
});
