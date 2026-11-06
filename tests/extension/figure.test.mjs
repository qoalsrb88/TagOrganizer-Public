// extension/editor/figure.js — node --test (tests/test_extension_core.py 가 부른다). 그린 모양은 e2e_nai.mjs 와 눈으로 본다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { BONES, FRACTION, LEANS, LYING, POSTURES, bodySamples, cameraBasis, cameraFrom, frameAxis, frameBox, gazeFrom, nearestBox,
  posedJoints, postureCamera, projector, resolvePose } from "../../extension/editor/figure.js";

const quick = JSON.parse(readFileSync(new URL("../../data/rules.json", import.meta.url), "utf-8")).quick;
const dial = quick.view_dial;
const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} ≠ ${b}`);
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

test("포즈 퀵 태그는 모두 자세나 기울기", () => {
  for (const tag of quick.pose) assert.ok(POSTURES[tag] || tag in LEANS, tag);
});

test("구체적인 자세를 쓰고 기울기는 선 자세에만", () => {
  assert.deepEqual(resolvePose([]), { posture: "standing", lean: 0 });
  assert.deepEqual(resolvePose(["lying", "on back"]), { posture: "on back", lean: 0 });
  assert.deepEqual(resolvePose(["sitting", "wariza"]), { posture: "wariza", lean: 0 });
  assert.deepEqual(resolvePose(["kneeling", "leaning forward"]), { posture: "kneeling", lean: 30 });
  assert.deepEqual(resolvePose(["leaning back"]), { posture: "standing", lean: -20 });
  assert.deepEqual(resolvePose(["on back", "bent over"]), { posture: "on back", lean: 0 });
  assert.deepEqual(resolvePose(["flying"]), { posture: "standing", lean: 0 });
});

test("자세마다 관절이 다 있고 바닥 위, 누운 자세는 옆으로 길다", () => {
  const front = projector({ azimuth: 0, elevation: 0 });
  for (const tag of [...Object.keys(POSTURES), "bent over"]) {
    const joints = posedJoints([tag]);
    assert.deepEqual(Object.keys(joints).sort(), Object.keys(FRACTION).sort(), tag);
    for (const [name, p] of Object.entries(joints)) {
      assert.ok(p.every(Number.isFinite), `${tag}.${name}`);
      assert.ok(p[1] > -0.01, `${tag}.${name} 가 바닥 아래`);
    }
    const samples = bodySamples(joints).map((s) => ({ ...s, s: front(s.p) }));
    const axis = frameAxis(frameBox(samples, [0, 1], front(joints.head), 0.065));
    assert.equal(axis, LYING.has(tag) ? "x" : "y", tag);
  }
  // 허리를 굽히면 머리가 골반 높이 가까이 내려온다
  const bent = posedJoints(["bent over"]);
  assert.ok(bent.head[1] < bent.pelvis[1] + 0.15 && bent.head[2] > 0.3);
});

test("카메라: 방향은 구간 at 의 평균, 높이·기울임", () => {
  assert.deepEqual(cameraFrom([], dial), { azimuth: 0, elevation: 0, roll: 0 });
  assert.equal(cameraFrom(["from behind"], dial).azimuth, 180);
  assert.equal(cameraFrom(["front view", "from side"], dial, 1).azimuth, 45);
  assert.equal(cameraFrom(["front view", "from side"], dial, -1).azimuth, -45);
  assert.equal(cameraFrom(["from above", "three quarter view"], dial).elevation, 50);
  assert.equal(cameraFrom(["dutch angle"], dial).roll, 12);
  assert.equal(postureCamera({ azimuth: 90, elevation: 0 }, "lying").azimuth, 0);
  assert.equal(postureCamera({ azimuth: -90, elevation: 0 }, "on side").azimuth, 0);
  assert.equal(postureCamera({ azimuth: 90, elevation: 0 }, "standing").azimuth, 90);
});

test("투영: 정면은 오른쪽이 오른쪽, 뒤에서는 뒤집히고, 위는 화면 위", () => {
  const front = projector({ azimuth: 0, elevation: 0 });
  close(front([1, 0, 0])[0], 1);
  close(front([0, 1, 0])[1], -1);
  close(front([0, 0, 1])[2], 1);
  const back = projector({ azimuth: 180, elevation: 0 });
  close(back([1, 0, 0])[0], -1);
  const above = cameraBasis({ azimuth: 0, elevation: 50 });
  assert.ok(above.toCamera[1] > 0.7);
  const tilted = projector({ azimuth: 0, elevation: 0, roll: 90 });
  close(tilted([1, 0, 0])[1], 1);
});

test("시선: 카메라를 보면 눈 맞춤, 등 뒤를 보면 고개가 돌 수 있는 만큼만", () => {
  const joints = posedJoints([]);
  const front = { azimuth: 0, elevation: 0 };
  const looking = gazeFrom(["looking at viewer"], joints, front);
  assert.equal(looking.contact, true);
  assert.equal(looking.closed, false);
  close(dot(looking.head, [0, 0, 1]), 1);
  const behind = gazeFrom(["looking at viewer"], joints, { azimuth: 180, elevation: 0 });
  close(dot(behind.head, [0, 0, 1]), Math.cos((100 * Math.PI) / 180), 1e-3);
  const away = gazeFrom(["facing away"], joints, front);
  assert.equal(away.closed, true);
  assert.ok(dot(away.head, [0, 0, 1]) < 0);
  const up = gazeFrom(["looking up"], joints, front);
  assert.ok(up.eyes[1] > 0.7 && up.head[1] > 0.3);
  const glance = gazeFrom(["sideways glance"], joints, front);
  close(dot(glance.head, [0, 0, 1]), 1);
  assert.ok(glance.eyes[0] > 0.9);
  assert.equal(gazeFrom([], joints, front).contact, false);
});

test("크롭 상자: 범위 안의 몸만, 가장 가까운 태그로 붙는다", () => {
  const project = projector({ azimuth: 0, elevation: 0 });
  const joints = posedJoints([]);
  const samples = bodySamples(joints).map((s) => ({ ...s, s: project(s.p) }));
  const head = project(joints.head);
  const boxes = Object.fromEntries(Object.entries(quick.framing_frame).map(([tag, span]) => [tag, frameBox(samples, span, head, 0.065)]));
  const full = boxes["full body"];
  assert.ok(boxes["upper body"][3] < boxes["cowboy shot"][3] && boxes["cowboy shot"][3] < full[3]);
  assert.ok(boxes["lower body"][1] > boxes["portrait"][3]);
  close(boxes["head out of frame"][3], full[3]);
  assert.ok(boxes["head out of frame"][1] > full[1] + 0.1);
  assert.equal(nearestBox(boxes, "y", full[1], full[3]), "full body");
  assert.equal(nearestBox(boxes, "y", boxes["lower body"][1] + 0.01, full[3]), "lower body");
  assert.equal(frameBox(samples, [0.995, 1], head, 0.065), null);
  assert.equal(BONES.length, 12);
});
