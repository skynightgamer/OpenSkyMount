// 3D visualisation of the simulated alt-az mount, driven by live telemetry.
// World frame (three.js, Y up): x = East, y = Up, z = South (North = -z). Units: metres.
// The model is illustrative (tripod, belt-driven fork, setting circles); it is not a CAD model of real hardware.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { getState, useStore } from "../store";

const DEG = Math.PI / 180;
const BG = 0x17191c;
const AZ_HEIGHT = 0.52; // top of the fixed azimuth base
const ALT_HEIGHT = 0.29; // altitude axis above the azimuth stage
const ARM_X = 0.13; // fork arm centre plane
const ARM_T = 0.022; // fork arm thickness
const PULLEY_X = -(ARM_X + ARM_T / 2 + 0.012); // plane of the altitude belt drive (outside the west arm)

// ------------------------------------------------------------------ materials
function materials() {
  return {
    anod: new THREE.MeshStandardMaterial({ color: 0x1d2024, metalness: 0.55, roughness: 0.42 }),
    alu: new THREE.MeshStandardMaterial({ color: 0xc3c8ce, metalness: 1, roughness: 0.28 }),
    leg: new THREE.MeshStandardMaterial({ color: 0x8e959d, metalness: 0.9, roughness: 0.38 }),
    rubber: new THREE.MeshStandardMaterial({ color: 0x101112, metalness: 0, roughness: 0.92 }),
    motor: new THREE.MeshStandardMaterial({ color: 0x2b2e33, metalness: 0.4, roughness: 0.55 }),
    belt: new THREE.MeshStandardMaterial({ color: 0x141414, metalness: 0, roughness: 0.85 }),
    index: new THREE.MeshStandardMaterial({ color: 0xd8473a, metalness: 0.1, roughness: 0.5 }),
    plastic: new THREE.MeshStandardMaterial({ color: 0x24272b, metalness: 0.05, roughness: 0.7 }),
  };
}
type Mats = ReturnType<typeof materials>;

// ------------------------------------------------------------------ geometry helpers
function mesh(geo: THREE.BufferGeometry, mat: THREE.Material, parent: THREE.Object3D, x = 0, y = 0, z = 0): THREE.Mesh {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  parent.add(m);
  return m;
}

/** Cylinder whose axis runs from `a` to `b`. */
function rod(a: THREE.Vector3, b: THREE.Vector3, r1: number, r2: number, mat: THREE.Material, parent: THREE.Object3D, seg = 20): THREE.Mesh {
  const m = mesh(new THREE.CylinderGeometry(r1, r2, a.distanceTo(b), seg), mat, parent);
  m.position.copy(a).add(b).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
  return m;
}

/** Cylinder along the x axis (shafts, hubs, knobs). */
function xCyl(r: number, len: number, mat: THREE.Material, parent: THREE.Object3D, x: number, y: number, z: number, seg = 32): THREE.Mesh {
  const m = mesh(new THREE.CylinderGeometry(r, r, len, seg), mat, parent, x, y, z);
  m.rotation.z = Math.PI / 2;
  return m;
}

/** Cylinder along the z axis (optical tubes; the payload looks along -z). */
function zCyl(r1: number, r2: number, z0: number, z1: number, mat: THREE.Material, parent: THREE.Object3D, open = false, seg = 48): THREE.Mesh {
  // CylinderGeometry's +y end gets r1; rotating by -90° about x sends +y to -z, so r1 sits at min(z0, z1).
  const m = mesh(new THREE.CylinderGeometry(r1, r2, Math.abs(z1 - z0), seg, 1, open), mat, parent, 0, 0, (z0 + z1) / 2);
  m.rotation.x = -Math.PI / 2;
  return m;
}

function roundedRect(x0: number, y0: number, x1: number, y1: number, r: number): THREE.Shape {
  const s = new THREE.Shape();
  s.moveTo(x0 + r, y0);
  s.lineTo(x1 - r, y0);
  s.quadraticCurveTo(x1, y0, x1, y0 + r);
  s.lineTo(x1, y1 - r);
  s.quadraticCurveTo(x1, y1, x1 - r, y1);
  s.lineTo(x0 + r, y1);
  s.quadraticCurveTo(x0, y1, x0, y1 - r);
  s.lineTo(x0, y0 + r);
  s.quadraticCurveTo(x0, y0, x0 + r, y0);
  return s;
}

/** Extrude a shape drawn in the (z, y) plane by `t` along x, centred on `x`. */
function sidePlate(shape: THREE.Shape, t: number, mat: THREE.Material, parent: THREE.Object3D, x: number, y = 0): THREE.Mesh {
  const bevel = Math.min(0.003, t / 4);
  const geo = new THREE.ExtrudeGeometry(shape, { depth: t - 2 * bevel, bevelEnabled: true, bevelSize: bevel, bevelThickness: bevel, bevelSegments: 2, curveSegments: 24 });
  geo.translate(0, 0, -(t - 2 * bevel) / 2);
  geo.rotateY(Math.PI / 2); // shape x -> world -z ... (symmetric shapes only), extrusion z -> world x
  return mesh(geo, mat, parent, x, y, 0);
}

/** Toothed disc (gear / timing pulley) in the xy plane, `t` thick along z. */
function gearGeometry(r: number, teeth: number, depth: number, t: number): THREE.ExtrudeGeometry {
  const s = new THREE.Shape();
  const n = teeth * 4;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2;
    const rr = i % 4 === 1 || i % 4 === 2 ? r : r - depth;
    if (i === 0) s.moveTo(rr * Math.cos(a), rr * Math.sin(a));
    else s.lineTo(rr * Math.cos(a), rr * Math.sin(a));
  }
  const geo = new THREE.ExtrudeGeometry(s, { depth: t, bevelEnabled: false });
  geo.translate(0, 0, -t / 2);
  return geo;
}

/** Flat triangular index mark in the xy plane, tip at the origin pointing -y, `t` thick along z. */
function pointerGeometry(len: number, w: number, t: number): THREE.ExtrudeGeometry {
  const s = new THREE.Shape();
  s.moveTo(0, 0);
  s.lineTo(w / 2, len);
  s.lineTo(-w / 2, len);
  s.lineTo(0, 0);
  return new THREE.ExtrudeGeometry(s, { depth: t, bevelEnabled: false });
}

// ------------------------------------------------------------------ canvas textures
function canvasTexture(size: number, draw: (g: CanvasRenderingContext2D, c: number) => void): THREE.CanvasTexture {
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  const g = cv.getContext("2d")!;
  draw(g, size / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/** Degree ring for a RingGeometry/CircleGeometry (planar UVs). `pos(deg)` gives the unit direction on the canvas. */
function scaleTexture(labelEvery: number, label: (deg: number) => string, pos: (deg: number) => [number, number]): THREE.CanvasTexture {
  return canvasTexture(1024, (g, c) => {
    const R = c;
    g.fillStyle = "#1b1e22";
    g.beginPath();
    g.arc(c, c, R, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "#e7e9ec";
    g.fillStyle = "#e7e9ec";
    for (let d = 0; d < 360; d++) {
      const len = d % 10 === 0 ? 0.075 : d % 5 === 0 ? 0.05 : 0.03;
      g.lineWidth = d % 10 === 0 ? 4 : 2.5;
      const [ux, uy] = pos(d);
      g.beginPath();
      g.moveTo(c + ux * R * 0.995, c + uy * R * 0.995);
      g.lineTo(c + ux * R * (0.995 - len), c + uy * R * (0.995 - len));
      g.stroke();
    }
    g.font = "600 30px system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (let d = 0; d < 360; d += labelEvery) {
      const [ux, uy] = pos(d);
      const rr = R * 0.87;
      g.save();
      g.translate(c + ux * rr, c + uy * rr);
      g.rotate(Math.atan2(ux, -uy));
      g.fillText(label(d), 0, 0);
      g.restore();
    }
  });
}

function groundTexture(): THREE.CanvasTexture {
  // Ground disc radius 3 m mapped onto the canvas; canvas up = North (-z), right = East (+x).
  return canvasTexture(2048, (g, c) => {
    const px = c / 3; // pixels per metre
    const grad = g.createRadialGradient(c, c, 0, c, c, c);
    grad.addColorStop(0, "#26292d");
    grad.addColorStop(1, "#17191c");
    g.fillStyle = grad;
    g.fillRect(0, 0, 2 * c, 2 * c);
    g.strokeStyle = "rgba(255,255,255,0.10)";
    g.lineWidth = 2;
    for (const r of [0.6, 1.15]) {
      g.beginPath();
      g.arc(c, c, r * px, 0, Math.PI * 2);
      g.stroke();
    }
    for (let d = 0; d < 360; d += 5) {
      const a = d * DEG;
      const r0 = 1.15 * px;
      const r1 = r0 + (d % 45 === 0 ? 0.09 : d % 15 === 0 ? 0.05 : 0.025) * px;
      g.strokeStyle = d === 0 ? "rgba(216,71,58,0.9)" : "rgba(255,255,255,0.22)";
      g.lineWidth = d % 45 === 0 ? 4 : 2;
      g.beginPath();
      g.moveTo(c + Math.sin(a) * r0, c - Math.cos(a) * r0);
      g.lineTo(c + Math.sin(a) * r1, c - Math.cos(a) * r1);
      g.stroke();
    }
    g.font = `600 ${0.13 * px}px system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (const [t, d] of [["N", 0], ["E", 90], ["S", 180], ["W", 270]] as const) {
      const a = d * DEG;
      const r = 1.38 * px;
      g.save();
      g.translate(c + Math.sin(a) * r, c - Math.cos(a) * r);
      g.rotate(a);
      g.fillStyle = d === 0 ? "rgba(216,71,58,0.95)" : "rgba(255,255,255,0.45)";
      g.fillText(t, 0, 0);
      g.restore();
    }
  });
}

// ------------------------------------------------------------------ fixed parts
function buildTripod(M: Mats, scene: THREE.Object3D) {
  const top = 0.405;
  const foot = 0.46;
  const legAt = (a: number, y: number) => {
    const t = (top - y) / top;
    const r = 0.07 + t * (foot - 0.07);
    return new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r);
  };
  // Head casting.
  const head = new THREE.LatheGeometry(
    [[0, 0.385], [0.06, 0.385], [0.078, 0.395], [0.082, 0.41], [0.08, 0.43], [0, 0.43]].map(([r, y]) => new THREE.Vector2(r, y)),
    48,
  );
  mesh(head, M.anod, scene);
  for (let i = 0; i < 3; i++) {
    const a = (i * 2 * Math.PI) / 3 + Math.PI / 6;
    // Hinge lug and pin.
    const lug = mesh(new RoundedBoxGeometry(0.04, 0.045, 0.036, 2, 0.006), M.anod, scene, Math.cos(a) * 0.082, 0.398, Math.sin(a) * 0.082);
    lug.rotation.y = -a;
    const c = new THREE.Vector3(Math.cos(a) * 0.09, 0.398, Math.sin(a) * 0.09);
    const tan = new THREE.Vector3(-Math.sin(a), 0, Math.cos(a)).multiplyScalar(0.022);
    rod(c.clone().sub(tan), c.clone().add(tan), 0.006, 0.006, M.alu, scene, 12);
    // Two-section leg with a twist-lock collar and rubber foot.
    const p0 = legAt(a, 0.39);
    const pMid = legAt(a, 0.17);
    const pEnd = legAt(a, 0.025);
    rod(p0, pMid, 0.016, 0.016, M.leg, scene);
    rod(pMid, pEnd, 0.0125, 0.0125, M.leg, scene);
    rod(pMid.clone().lerp(p0, 0.08), pMid.clone().lerp(pEnd, 0.12), 0.0205, 0.0195, M.rubber, scene);
    rod(pEnd, legAt(a, 0), 0.016, 0.02, M.rubber, scene, 16);
  }
  // Spreader tray (three arms meeting at a hub) with the controller box on it.
  const trayY = 0.13;
  mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.012, 32), M.anod, scene, 0, trayY, 0);
  for (let i = 0; i < 3; i++) {
    const a = (i * 2 * Math.PI) / 3 + Math.PI / 6;
    const end = legAt(a, trayY);
    const len = Math.hypot(end.x, end.z) - 0.05;
    const arm = mesh(new RoundedBoxGeometry(len, 0.008, 0.022, 2, 0.003), M.anod, scene, Math.cos(a) * (0.03 + len / 2), trayY, Math.sin(a) * (0.03 + len / 2));
    arm.rotation.y = -a;
    rod(end.clone().setY(trayY + 0.012), end.clone().setY(trayY - 0.012), 0.02, 0.02, M.anod, scene);
  }
  const box = mesh(new RoundedBoxGeometry(0.1, 0.035, 0.07, 3, 0.006), M.plastic, scene, 0, trayY + 0.0235, 0.01);
  box.name = "controller";
  const led = mesh(new THREE.SphereGeometry(0.0035, 12, 8), new THREE.MeshStandardMaterial({ color: 0x113311, emissive: 0x33ff55, emissiveIntensity: 1.5 }), scene, 0.035, trayY + 0.03, 0.0455);
  led.name = "status-led";
  // Cable from the controller up to the azimuth drive housing.
  const cable = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-0.03, trayY + 0.035, 0.045),
    new THREE.Vector3(-0.03, trayY + 0.07, 0.11),
    new THREE.Vector3(-0.015, 0.33, 0.2),
    new THREE.Vector3(0, 0.425, 0.205),
  ]);
  mesh(new THREE.TubeGeometry(cable, 40, 0.0035, 8), M.rubber, scene);
}

function buildAzBase(M: Mats, scene: THREE.Object3D) {
  const prof: [number, number][] = [[0, 0.43], [0.112, 0.43], [0.132, 0.436], [0.14, 0.448], [0.14, 0.506], [0.1455, 0.512], [0.1455, AZ_HEIGHT], [0, AZ_HEIGHT]];
  mesh(new THREE.LatheGeometry(prof.map(([r, y]) => new THREE.Vector2(r, y)), 96), M.anod, scene);
  // Azimuth setting circle: 0 at North (canvas up), increasing through East (canvas right).
  const ring = mesh(
    new THREE.RingGeometry(0.121, 0.1452, 128, 1),
    new THREE.MeshStandardMaterial({
      map: scaleTexture(30, (d) => String(d), (d) => [Math.sin(d * DEG), -Math.cos(d * DEG)]),
      metalness: 0.3,
      roughness: 0.55,
    }),
    scene,
    0,
    AZ_HEIGHT + 0.0006,
    0,
  );
  ring.rotation.x = -Math.PI / 2;
  // Drive housing on the south side with the stepper hanging below it.
  mesh(new RoundedBoxGeometry(0.075, 0.05, 0.06, 3, 0.008), M.anod, scene, 0, 0.476, 0.163);
  stepper(M, scene, new THREE.Vector3(0, 0.428, 0.168), "y");
}

/** NEMA-17-style stepper: anodised end caps around a laminated body, shaft along +axis. */
function stepper(M: Mats, parent: THREE.Object3D, at: THREE.Vector3, axis: "x" | "y" | "-x") {
  const g = new THREE.Group();
  g.position.copy(at);
  if (axis === "x") g.rotation.z = -Math.PI / 2;
  if (axis === "-x") g.rotation.z = Math.PI / 2;
  parent.add(g);
  const s = 0.042;
  mesh(new RoundedBoxGeometry(s, 0.008, s, 2, 0.004), M.alu, g, 0, 0.017, 0);
  mesh(new RoundedBoxGeometry(s * 0.97, 0.026, s * 0.97, 2, 0.003), M.motor, g, 0, 0, 0);
  mesh(new RoundedBoxGeometry(s, 0.008, s, 2, 0.004), M.alu, g, 0, -0.017, 0);
  mesh(new THREE.CylinderGeometry(0.011, 0.011, 0.003, 24), M.alu, g, 0, 0.0225, 0);
  mesh(new THREE.CylinderGeometry(0.0025, 0.0025, 0.024, 12), M.alu, g, 0, 0.033, 0);
  return g;
}

// ------------------------------------------------------------------ rotating stages
function buildAzStage(M: Mats, az: THREE.Group) {
  // Turntable with a bolt circle.
  const tt: [number, number][] = [[0, 0], [0.116, 0], [0.12, 0.004], [0.12, 0.026], [0.116, 0.03], [0, 0.03]];
  mesh(new THREE.LatheGeometry(tt.map(([r, y]) => new THREE.Vector2(r, y)), 96), M.alu, az);
  for (let i = 0; i < 8; i++) {
    const a = ((i + 0.5) / 8) * Math.PI * 2;
    mesh(new THREE.CylinderGeometry(0.0045, 0.0045, 0.004, 6), M.anod, az, Math.cos(a) * 0.105, 0.032, Math.sin(a) * 0.105);
  }
  // Index mark reading the azimuth circle (local -z is the pointing direction at azimuth 0).
  // Shape +y -> +z (base towards the centre), extrusion -> downwards; the tip lies over the ticks.
  const idx = mesh(pointerGeometry(0.02, 0.012, 0.0015), M.index, az, 0, 0.0025, -0.137);
  idx.rotation.x = Math.PI / 2;
  // Fork base plate.
  const base = new THREE.ExtrudeGeometry(roundedRect(-0.155, -0.07, 0.155, 0.07, 0.03), { depth: 0.022, bevelEnabled: true, bevelSize: 0.003, bevelThickness: 0.003, curveSegments: 12 });
  base.rotateX(Math.PI / 2);
  mesh(base, M.anod, az, 0, 0.055, 0);
  // Tapered fork arms with a lightening slot, drawn in the (z, y) plane.
  const h = ALT_HEIGHT - 0.058;
  const arm = new THREE.Shape();
  arm.moveTo(-0.062, 0);
  arm.lineTo(0.062, 0);
  arm.lineTo(0.038, h);
  arm.absarc(0, h, 0.038, 0, Math.PI, false);
  arm.lineTo(-0.062, 0);
  const slot = new THREE.Path();
  slot.moveTo(-0.017, 0.07);
  slot.lineTo(-0.017, 0.15);
  slot.absarc(0, 0.15, 0.017, Math.PI, 0, true);
  slot.lineTo(0.017, 0.07);
  slot.absarc(0, 0.07, 0.017, 0, -Math.PI, true);
  arm.holes.push(slot);
  for (const side of [-1, 1]) {
    sidePlate(arm, ARM_T, M.anod, az, side * ARM_X, 0.058);
    // Bearing housings.
    xCyl(0.027, 0.008, M.alu, az, side * (ARM_X - ARM_T / 2 - 0.004), ALT_HEIGHT, 0);
  }
  xCyl(0.024, 0.01, M.alu, az, ARM_X + ARM_T / 2 + 0.005, ALT_HEIGHT, 0);
  xCyl(0.009, 0.006, M.anod, az, ARM_X + ARM_T / 2 + 0.012, ALT_HEIGHT, 0, 6); // axle nut

  // Altitude belt drive on the west arm: stepper inside the arm, pulley + belt outside.
  const motorY = 0.105;
  stepper(M, az, new THREE.Vector3(-(ARM_X - ARM_T / 2 - 0.021), motorY, 0), "-x");
  xCyl(0.0025, 0.05, M.alu, az, PULLEY_X + 0.012, motorY, 0, 12);
  const small = mesh(gearGeometry(0.0105, 20, 0.0012, 0.009), M.alu, az, PULLEY_X, motorY, 0);
  small.rotation.y = Math.PI / 2;
  for (const dx of [-0.0055, 0.0055]) xCyl(0.0135, 0.0015, M.alu, az, PULLEY_X + dx, motorY, 0);
  belt(M, az, ALT_HEIGHT, 0.0565, motorY, 0.0115);
  // Fixed index mark above the altitude scale.
  const altIdx = mesh(pointerGeometry(0.022, 0.01, 0.0015), M.index, az, PULLEY_X - 0.0085, ALT_HEIGHT + 0.049, 0);
  altIdx.rotation.y = -Math.PI / 2; // shape plane -> (z, y), extrusion -> -x (outwards)
  const bracketX0 = -(ARM_X + ARM_T / 2);
  const bracketX1 = PULLEY_X - 0.0085;
  mesh(new RoundedBoxGeometry(bracketX0 - bracketX1, 0.008, 0.014, 2, 0.002), M.anod, az, (bracketX0 + bracketX1) / 2, ALT_HEIGHT + 0.075, 0);
}

/** Timing belt around two pulleys stacked vertically in the plane x = PULLEY_X. */
function belt(M: Mats, parent: THREE.Object3D, y1: number, r1: number, y2: number, r2: number) {
  const d = y1 - y2;
  const beta = Math.asin((r1 - r2) / d);
  const w = 0.008;
  const t = 0.0018;
  const arc = (yc: number, r: number, start: number, len: number) => {
    const geo = new THREE.TorusGeometry(r, t, 4, 64, len);
    geo.scale(1, 1, w / (2 * t)); // flat belt: wide along the pulley axis
    geo.rotateZ(start);
    const m = mesh(geo, M.belt, parent, PULLEY_X, yc, 0);
    m.rotation.y = -Math.PI / 2; // torus plane -> (z, y), angle measured from +z towards +y
  };
  arc(y1, r1, -beta, Math.PI + 2 * beta);
  arc(y2, r2, Math.PI + beta, Math.PI - 2 * beta);
  for (const s of [-1, 1]) {
    // Tangent points: P = C - r n with n = (s cos β, sin β) in (z, y).
    const nz = s * Math.cos(beta);
    const ny = Math.sin(beta);
    const a = new THREE.Vector3(PULLEY_X, y1 - r1 * ny, -r1 * nz);
    const b = new THREE.Vector3(PULLEY_X, y2 - r2 * ny, -r2 * nz);
    const seg = mesh(new THREE.BoxGeometry(w, a.distanceTo(b), 2 * t), M.belt, parent);
    seg.position.copy(a).add(b).multiplyScalar(0.5);
    seg.rotation.x = Math.atan2(a.z - b.z, a.y - b.y);
  }
}

function buildAltStage(M: Mats, alt: THREE.Group) {
  // Axle through both bearings.
  // Stub axles from each bearing to the payload cradle (no through-shaft, so the payload space stays clear).
  xCyl(0.008, 0.06, M.alu, alt, 0.126, 0, 0, 16);
  xCyl(0.008, 0.07, M.alu, alt, -0.131, 0, 0, 16);
  // Large timing pulley with the altitude setting circle on its outer face.
  const big = mesh(gearGeometry(0.0555, 72, 0.0015, 0.009), M.alu, alt, PULLEY_X, 0, 0);
  big.rotation.y = Math.PI / 2;
  for (const dx of [-0.0055, 0.0055]) xCyl(0.0585, 0.0015, M.anod, alt, PULLEY_X + dx, 0, 0, 64);
  // Value θ sits at (y, z) = (cos θ, -sin θ): it reaches the fixed index at the top when the alt axis reads θ.
  const face = mesh(
    new THREE.CircleGeometry(0.0545, 96),
    new THREE.MeshStandardMaterial({
      map: scaleTexture(30, (d) => String(d > 180 ? d - 360 : d).replace("-", "−"), (d) => [-Math.sin(d * DEG), -Math.cos(d * DEG)]),
      metalness: 0.3,
      roughness: 0.55,
    }),
    alt,
    PULLEY_X - 0.0068,
    0,
    0,
  );
  face.rotation.y = -Math.PI / 2;
  xCyl(0.012, 0.004, M.alu, alt, PULLEY_X - 0.008, 0, 0); // centre cap
}

// ------------------------------------------------------------------ payloads
/** U-shaped cradle hanging from the altitude axle: side plates at ±0.1 m and a saddle `depth` below the axis. */
function cradle(M: Mats, g: THREE.Group, depth: number, zLen: number, saddle = true) {
  const plate = roundedRect(-zLen / 2, -depth - 0.012, zLen / 2, 0.026, 0.014);
  for (const side of [-1, 1]) {
    sidePlate(plate, 0.008, M.anod, g, side * 0.1);
    xCyl(0.014, 0.006, M.alu, g, side * 0.106, 0, 0); // clamp collar on the axle
  }
  if (saddle) mesh(new RoundedBoxGeometry(0.208, 0.012, zLen, 2, 0.003), M.anod, g, 0, -depth - 0.006, 0);
}

function buildPayload(shape: string, M: Mats): THREE.Group {
  const g = new THREE.Group();
  const paint = new THREE.MeshStandardMaterial({ color: 0xe9ebee, metalness: 0.1, roughness: 0.32 });
  const black = new THREE.MeshStandardMaterial({ color: 0x141518, metalness: 0.2, roughness: 0.6 });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x0e1a24, metalness: 0.2, roughness: 0.05, clearcoat: 1, clearcoatRoughness: 0.05 });
  const inside = new THREE.MeshStandardMaterial({ color: 0x050505, roughness: 1, side: THREE.BackSide });
  const lensCap = (r: number, z: number) => {
    const m = mesh(new THREE.CircleGeometry(r, 48), glass, g, 0, 0, z);
    m.rotation.y = Math.PI; // face -z
  };
  const dslr = (s: number, z: number) => {
    mesh(new RoundedBoxGeometry(0.135 * s, 0.095 * s, 0.07 * s, 3, 0.012 * s), black, g, 0, 0, z + 0.035 * s);
    mesh(new RoundedBoxGeometry(0.034 * s, 0.098 * s, 0.085 * s, 3, 0.012 * s), black, g, 0.06 * s, -0.001, z + 0.028 * s); // grip
    const prism = mesh(new THREE.CylinderGeometry(0.025 * s, 0.04 * s, 0.032 * s, 4, 1), black, g, 0, 0.058 * s, z + 0.035 * s);
    prism.rotation.y = Math.PI / 4;
    prism.scale.set(1, 1, 0.75);
    const dial = mesh(new THREE.CylinderGeometry(0.012 * s, 0.012 * s, 0.008 * s, 24), M.alu, g, -0.045 * s, 0.05 * s, z + 0.04 * s);
    dial.rotation.y = 0.3;
    mesh(new THREE.CylinderGeometry(0.0055 * s, 0.0055 * s, 0.004 * s, 16), M.index, g, 0.055 * s, 0.049 * s, z + 0.012 * s); // shutter button
    zCyl(0.031 * s, 0.031 * s, z - 0.006 * s, z, M.alu, g); // lens mount
  };
  switch (shape) {
    case "telescope": {
      // 80 mm refractor (about 0.5 m overall): white tube, dew shield, focuser, star diagonal, eyepiece, finder.
      cradle(M, g, 0.075, 0.12);
      mesh(new RoundedBoxGeometry(0.036, 0.014, 0.22, 2, 0.003), M.alu, g, 0, -0.066, -0.01); // dovetail
      for (const z of [-0.08, 0.06]) {
        const ring = mesh(new THREE.TorusGeometry(0.051, 0.005, 12, 64), M.anod, g, 0, 0, z);
        ring.scale.set(1, 1, 1.8);
        mesh(new RoundedBoxGeometry(0.022, 0.012, 0.018, 2, 0.003), M.anod, g, 0, -0.055, z);
        xCyl(0.004, 0.014, M.alu, g, 0.04, 0.035, z, 12); // ring clamp knob
        mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.026, 8), M.alu, g, 0, 0.068, z); // finder post
      }
      zCyl(0.046, 0.046, -0.17, 0.13, paint, g);
      zCyl(0.052, 0.052, -0.27, -0.17, black, g, true); // dew shield
      zCyl(0.0505, 0.0505, -0.27, -0.17, inside, g, true);
      zCyl(0.054, 0.054, -0.275, -0.267, M.alu, g, true);
      zCyl(0.048, 0.048, -0.18, -0.165, black, g); // objective cell
      lensCap(0.044, -0.181);
      zCyl(0.047, 0.04, 0.13, 0.15, black, g); // rear cell
      zCyl(0.026, 0.026, 0.15, 0.18, black, g); // focuser body
      xCyl(0.009, 0.075, M.alu, g, 0, -0.022, 0.165, 24); // focus knobs
      zCyl(0.019, 0.019, 0.18, 0.21, M.alu, g); // drawtube
      mesh(new RoundedBoxGeometry(0.036, 0.036, 0.036, 2, 0.005), black, g, 0, 0, 0.228); // star diagonal
      mesh(new THREE.CylinderGeometry(0.013, 0.013, 0.05, 32), black, g, 0, 0.043, 0.228); // eyepiece
      mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.01, 32), new THREE.MeshStandardMaterial({ color: 0x2a2c30, roughness: 0.95 }), g, 0, 0.072, 0.228);
      const f = new THREE.Group();
      f.position.set(0, 0.085, 0);
      g.add(f);
      zCyl(0.011, 0.011, -0.09, 0.06, black, f);
      zCyl(0.013, 0.011, -0.11, -0.09, black, f);
      zCyl(0.006, 0.006, 0.06, 0.08, black, f);
      break;
    }
    case "telephoto": {
      cradle(M, g, 0.07, 0.07);
      mesh(new RoundedBoxGeometry(0.03, 0.03, 0.06, 2, 0.004), paint, g, 0, -0.053, 0); // tripod collar foot
      zCyl(0.05, 0.05, -0.03, 0.03, paint, g); // collar
      dslr(1, 0.1);
      zCyl(0.044, 0.044, -0.06, 0.1, paint, g);
      zCyl(0.049, 0.049, -0.17, -0.06, black, g); // focus ring
      zCyl(0.052, 0.052, -0.3, -0.17, paint, g);
      zCyl(0.06, 0.06, -0.4, -0.3, black, g, true); // hood
      zCyl(0.0585, 0.0585, -0.4, -0.3, inside, g, true);
      zCyl(0.053, 0.053, -0.305, -0.295, M.index, g); // red ring
      lensCap(0.05, -0.305);
      break;
    }
    case "dslr":
    case "camera": {
      const s = shape === "dslr" ? 1 : 0.78;
      cradle(M, g, 0.05 * s + 0.004, 0.08);
      dslr(s, 0.0);
      zCyl(0.034 * s, 0.034 * s, -0.04 * s, -0.006 * s, black, g);
      zCyl(0.037 * s, 0.037 * s, -0.08 * s, -0.04 * s, new THREE.MeshStandardMaterial({ color: 0x0c0c0d, roughness: 0.95 }), g);
      zCyl(0.035 * s, 0.035 * s, -0.11 * s, -0.08 * s, black, g);
      lensCap(0.03 * s, -0.111 * s);
      break;
    }
    default: {
      // Smartphone in a spring clamp. The main camera lens sits on the optical (pointing) axis.
      const phone = new THREE.Group();
      phone.position.set(-0.022, -0.058, 0);
      g.add(phone);
      mesh(new RoundedBoxGeometry(0.075, 0.158, 0.009, 4, 0.0042), new THREE.MeshStandardMaterial({ color: 0x59606b, metalness: 0.7, roughness: 0.3 }), phone);
      const screen = mesh(new THREE.PlaneGeometry(0.069, 0.152), new THREE.MeshStandardMaterial({ color: 0x050608, emissive: 0x0b1420, roughness: 0.15 }), phone, 0, 0, 0.0047);
      screen.name = "screen";
      mesh(new RoundedBoxGeometry(0.036, 0.036, 0.004, 3, 0.008), new THREE.MeshStandardMaterial({ color: 0x3a3f46, metalness: 0.5, roughness: 0.35 }), phone, 0.022, 0.058, -0.0055);
      const main = mesh(new THREE.CylinderGeometry(0.0075, 0.0075, 0.003, 32), M.anod, phone, 0.022, 0.058, -0.008);
      main.rotation.x = Math.PI / 2;
      const gl = mesh(new THREE.CircleGeometry(0.0052, 32), glass, phone, 0.022, 0.058, -0.0096);
      gl.rotation.y = Math.PI;
      for (const [x, y] of [[0.011, 0.048], [0.011, 0.068]]) {
        const lens = mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.002, 24), M.anod, phone, x, y, -0.0075);
        lens.rotation.x = Math.PI / 2;
      }
      // Spring clamp across the middle of the phone, on a crossbar between the cradle plates.
      cradle(M, g, 0.07, 0.05, false);
      xCyl(0.006, 0.2, M.alu, g, 0, -0.058, -0.018, 16);
      mesh(new RoundedBoxGeometry(0.09, 0.024, 0.01, 2, 0.003), M.plastic, phone, 0, 0, -0.011);
      for (const sx of [-1, 1]) mesh(new RoundedBoxGeometry(0.008, 0.026, 0.018, 2, 0.003), M.plastic, phone, sx * 0.0415, 0, -0.002);
    }
  }
  return g;
}

function disposeTree(o: THREE.Object3D, keep: Set<THREE.Material> = new Set()) {
  o.traverse((c) => {
    if (c instanceof THREE.Mesh || c instanceof THREE.Line) {
      c.geometry.dispose();
      const mats = Array.isArray(c.material) ? c.material : [c.material];
      for (const m of mats) {
        if (keep.has(m)) continue;
        (m as THREE.MeshStandardMaterial).map?.dispose();
        m.dispose();
      }
    }
  });
}

function enableShadows(o: THREE.Object3D) {
  o.traverse((c) => {
    if (c instanceof THREE.Mesh) {
      c.castShadow = true;
      c.receiveShadow = true;
    }
  });
}

export default function MountView() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const payloadShape = useStore((s) => {
    const c = s.config;
    return c ? c.payloads[c.config.payload]?.shape ?? "phone" : "phone";
  });
  const payloadRef = useRef<{ holder: THREE.Group | null; shape: string; mats: Mats | null }>({ holder: null, shape: "", mats: null });

  useEffect(() => {
    const wrap = wrapRef.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    wrap.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG);
    scene.fog = new THREE.Fog(BG, 3.2, 7);
    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    scene.environmentIntensity = 0.55;

    const camera = new THREE.PerspectiveCamera(35, 1, 0.02, 100);
    camera.position.set(1.0, 1.05, 1.3);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.62, 0);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.55;
    controls.minDistance = 0.45;
    controls.maxDistance = 6;

    scene.add(new THREE.HemisphereLight(0xcfdcff, 0x1c1712, 0.45));
    const key = new THREE.DirectionalLight(0xfff4e6, 2.2);
    key.position.set(1.6, 3.2, 1.4);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -1, right: 1, top: 1.2, bottom: -0.6, near: 0.5, far: 8 });
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.01;
    key.shadow.camera.updateProjectionMatrix();
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x9db8ff, 0.7);
    rim.position.set(-2, 1.4, -2);
    scene.add(rim);

    const ground = new THREE.Mesh(new THREE.CircleGeometry(3, 96), new THREE.MeshStandardMaterial({ map: groundTexture(), roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);

    const M = materials();
    payloadRef.current.mats = M;
    const mount = new THREE.Group();
    scene.add(mount);
    buildTripod(M, mount);
    buildAzBase(M, mount);
    const azGroup = new THREE.Group();
    azGroup.position.y = AZ_HEIGHT;
    mount.add(azGroup);
    buildAzStage(M, azGroup);
    const altGroup = new THREE.Group();
    altGroup.position.y = ALT_HEIGHT;
    azGroup.add(altGroup);
    buildAltStage(M, altGroup);
    const holder = new THREE.Group();
    altGroup.add(holder);
    payloadRef.current.holder = holder;
    payloadRef.current.shape = "";
    enableShadows(mount);

    // Pointing ray (actual) and target direction.
    const ray = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, -0.05), new THREE.Vector3(0, 0, -2.4)]),
      new THREE.LineBasicMaterial({ color: 0xff5b7a }),
    );
    altGroup.add(ray);
    const targetGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const targetLine = new THREE.Line(targetGeo, new THREE.LineDashedMaterial({ color: 0xffb347, dashSize: 0.05, gapSize: 0.035 }));
    targetLine.visible = false;
    scene.add(targetLine);
    const pivot = new THREE.Vector3(0, AZ_HEIGHT + ALT_HEIGHT, 0);

    const resize = () => {
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = `${w}px`;
      renderer.domElement.style.height = `${h}px`;
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const m = getState().mount;
      if (m) {
        // Physical axis angles (simulation ground truth when available).
        const altAxis = m.driver.truth?.alt_axis ?? m.position.alt_axis;
        const azAxis = m.driver.truth?.az_axis ?? m.position.az_axis;
        azGroup.rotation.y = -azAxis * DEG;
        altGroup.rotation.x = altAxis * DEG;
        if (m.target) {
          const a = m.target.alt * DEG;
          const z = m.target.az * DEG;
          const dir = new THREE.Vector3(Math.cos(a) * Math.sin(z), Math.sin(a), -Math.cos(a) * Math.cos(z));
          targetGeo.setFromPoints([pivot, pivot.clone().addScaledVector(dir, 2.4)]);
          targetLine.computeLineDistances();
          targetLine.visible = true;
        } else targetLine.visible = false;
      }
      controls.update();
      renderer.render(scene, camera);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      disposeTree(scene);
      envTex.dispose();
      pmrem.dispose();
      renderer.dispose();
      wrap.removeChild(renderer.domElement);
      payloadRef.current.holder = null;
    };
  }, []);

  useEffect(() => {
    const ref = payloadRef.current;
    if (!ref.holder || !ref.mats || ref.shape === payloadShape) return;
    disposeTree(ref.holder, new Set(Object.values(ref.mats)));
    ref.holder.clear();
    const p = buildPayload(payloadShape, ref.mats);
    enableShadows(p);
    ref.holder.add(p);
    ref.shape = payloadShape;
  });

  return (
    <div className="mount-wrap" ref={wrapRef}>
      <div className="mount-legend">
        <span>
          <i className="swatch swatch-actual" /> Payload axis
        </span>
        <span>
          <i className="swatch swatch-target" /> Target direction
        </span>
      </div>
    </div>
  );
}
