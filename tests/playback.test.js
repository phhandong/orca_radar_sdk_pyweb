"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Playback, frameAt, filterDomains, matchesFilters, clampFilterBound } = require("../web/player.js");
const frames = [0, .1, .5, 2].map(time => ({ time }));

test("irregular timestamps select the last frame at or before playhead", () => {
  assert.equal(frameAt(frames, .4), 1);
  assert.equal(frameAt(frames, .5), 2);
  assert.equal(frameAt(frames, 2), 3);
});
test("pause, seek and speed changes keep a continuous recording clock", () => {
  const p = new Playback(frames);
  p.toggle(1000); p.tick(1400);
  assert.equal(p.index, 1);
  p.toggle(1400); p.tick(5000);
  assert.equal(p.position, .4);
  p.seek(.6, 5000); assert.equal(p.index, 2);
  assert.equal(p.playing, false);
  p.toggle(5000); p.setSpeed(2, 5100); p.tick(5200);
  assert.ok(Math.abs(p.position - .9) < 1e-9);
});
test("end stops on last frame and play restarts", () => {
  const p = new Playback(frames);
  p.toggle(0); p.tick(3000);
  assert.equal(p.position, 2); assert.equal(p.index, 3); assert.equal(p.playing, false);
  p.toggle(3000); assert.equal(p.position, 0); assert.equal(p.playing, true);
});
test("loop preserves overshoot, single-frame loop terminates", () => {
  const p = new Playback(frames); p.loop = true;
  p.toggle(0); p.tick(2500);
  assert.equal(p.position, .5); assert.equal(p.index, 2); assert.equal(p.playing, true);
  const single = new Playback([{ time: 0 }]); single.loop = true;
  single.toggle(0); single.tick(100);
  assert.equal(single.position, 0); assert.equal(single.playing, false);
});
test("frame stepping can inspect duplicate timestamps", () => {
  const p = new Playback([0, 0, .1].map(time => ({ time })));
  p.step(1, 0); assert.equal(p.index, 1);
  p.step(-1, 0); p.tick(500); assert.equal(p.index, 0);
  p.seek(100, 500); assert.equal(p.position, .1);
  p.seek(-100, 500); assert.equal(p.position, 0);
});

test("filter ranges include every frame, negative velocity, and fractional endpoints", () => {
  const frames = [{ points: [] }, { points: [[0, 0, 0, -1.234, 1, 2]] },
    { points: [[0, 0, 0, 2.345, 3, 1.5]] }];
  const domains = filterDomains(frames);
  assert.deepEqual(domains, [[-1.24, 2.35], [1, 3], [1.5, 2]]);
  assert.ok(frames.every(f => f.points.every(p => matchesFilters(p, domains))));
  assert.deepEqual(filterDomains([{ points: [] }]), [[0, 0], [0, 0], [0, 0]]);
  assert.deepEqual(filterDomains([{ points: [[0, 0, 0, 0, 5, 5]] }]), [[0, 0], [5, 5], [5, 5]]);
  assert.throws(() => filterDomains([{ points: [[0, 0, 0, 1]] }]), /重启/);
});

test("all three inclusive ranges apply without changing raw points", () => {
  const ranges = [[-2, 0], [1, 2], [.5, 1]];
  const point = [1, 2, 3, -2, 1, 1];
  assert.ok(matchesFilters(point, ranges));
  assert.ok(matchesFilters([1, 2, 3, 0, 2, .5], ranges));
  for (const [column, value] of [[3, .01], [3, -2.01], [4, .9], [4, 2.1], [5, .4], [5, 1.1]]) {
    const excluded = [...point]; excluded[column] = value;
    assert.equal(matchesFilters(excluded, ranges), false);
  }
  assert.deepEqual(point, [1, 2, 3, -2, 1, 1]);
});

test("range handles cannot cross or escape file bounds, equal bounds are allowed", () => {
  assert.deepEqual(clampFilterBound([2, 8], [0, 10], 0, 9), [8, 8]);
  assert.deepEqual(clampFilterBound([2, 8], [0, 10], 1, 1), [2, 2]);
  assert.deepEqual(clampFilterBound([2, 8], [0, 10], 0, -2), [0, 8]);
  assert.deepEqual(clampFilterBound([2, 8], [0, 10], 1, 12), [2, 10]);
  assert.deepEqual(clampFilterBound([2, 8], [0, 10], 0, NaN), [2, 8]);
});
